"""Admin routes — panel de administración: usuarios, recursos globales,
configuración de plataforma y auto-actualización.

Extraído de auth.py (que se estaba acercando a las 2000 líneas mezclando
sesión de usuario y administración) para que un fix de ownership/acceso en
un sitio no se quede sin replicar en el otro por simple tamaño del archivo.
Las dependencias de autenticación (`require_admin`, `require_auth`,
`GroupContext`, etc.) siguen viviendo en auth.py — son el contrato que
importan ~18 archivos de todo el backend y moverlas habría sido un cambio de
alto riesgo sin beneficio real.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from app.api.routes.auth import _public_base_url, require_admin
from app.auth.auth import (
    admin_set_password,
    admin_update_user,
    create_token,
    delete_user,
    get_user_by_username,
    hash_password,
    list_users,
    send_account_status_email,
)
from app.config.data import AGENTS_DIR as _AGENTS_DIR
from app.config.data import DB_FILE as _DB_FILE
from app.config.session import SECURE_COOKIES
from app.errors import APIError
from app.storage.db import IS_PG, open_db
from app.storage.storage import AgentStorage as _AgentStorage
from app.storage.workflows import WorkflowStorage as _WorkflowStorage
from app.storage.groups import GroupStorage as _GroupStorage
from app.utils import flog

_groups = _GroupStorage(_DB_FILE)
_agents = _AgentStorage(_AGENTS_DIR)
_workflows = _WorkflowStorage()

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


def _version_re(variant: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(variant)}-(\d{{14}})$")


async def _latest_docker_hub_version(repo: str, variant: str) -> str | None:
    """Versión (tag `<variant>-YYYYMMDDHHMMSS`) más reciente publicada en Docker
    Hub para `repo`, restringida a `variant` ("react" o "vanilla").

    `iagenthub/app` recibe pushes desde 3 workflows distintos (iAgents,
    frontend_react, frontend_vanilla) que generan cada uno su propio timestamp
    de build para react y para vanilla — sin el prefijo de variante, comparar
    el máximo global mezclaría ambas familias de tags y detectaría "update
    available" con solo que la OTRA variante se hubiese publicado un segundo
    antes, aunque la que está desplegada siga totalmente al día.

    Usa la API pública de Docker Hub (hub.docker.com/v2), no el registry — sin
    autenticación y sin el rate-limit estricto de docker.io/pulls. None si el
    repo no tiene ningún tag de esa variante todavía.
    """
    pattern = _version_re(variant)
    versions: list[str] = []
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100"
    async with httpx.AsyncClient(timeout=10) as client:
        for _ in range(10):  # límite de páginas por seguridad, no debería hacer falta
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            for t in data.get("results", []):
                match = pattern.match(t.get("name", ""))
                if match:
                    versions.append(match.group(1))
            url = data.get("next")
            if not url:
                break
    return max(versions) if versions else None


async def _latest_github_commit_sha(repo: str, branch: str = "main") -> str | None:
    """SHA completo del último commit en `branch` de `repo` (API pública de
    GitHub, sin autenticación). None si la consulta falla por cualquier motivo
    — a diferencia de Docker Hub, esta comprobación es solo informativa
    (distingue si lo desactualizado es el backend o el frontend) y nunca debe
    tumbar el resto de /check-update.
    """
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            return resp.json().get("sha")
    except httpx.HTTPError:
        return None


@admin_router.get("/check-update")
async def admin_check_update(_: str = Depends(require_admin)) -> dict:
    """Compara la versión de la imagen en ejecución (GAIA_VERSION) contra la
    más reciente publicada en Docker Hub. Solo informa — no aplica el update
    (eso lo hace Watchtower, o `docker compose pull && up -d` manual).

    GAIA_VERSION solo dice "cuándo" se construyó la imagen, no "qué" código de
    backend_fastapi ni del frontend lleva dentro — así que además se compara
    el commit horneado de cada repo (BACKEND_COMMIT/FRONTEND_COMMIT) contra el
    HEAD de main en GitHub, para saber cuál de los dos está desactualizado.
    """
    current_version = os.environ.get("GAIA_VERSION", "dev")
    if current_version == "dev":
        return {
            "checked": False,
            "reason": "no_version",
            "current_version": current_version,
        }

    hub_user = os.environ.get("DOCKER_HUB_USER", "iagenthub")
    repo = f"{hub_user}/app"
    variant = "vanilla" if os.environ.get("IMAGE_TAG") == "vanilla" else "react"
    try:
        latest_version = await _latest_docker_hub_version(repo, variant)
    except httpx.HTTPError as exc:
        raise APIError(
            502, "check_update_failed", "No se pudo consultar Docker Hub"
        ) from exc

    backend_commit = os.environ.get("BACKEND_COMMIT", "dev")
    frontend_commit = os.environ.get("FRONTEND_COMMIT", "dev")
    frontend_repo = f"iagentshub/frontend_{variant}"
    backend_latest, frontend_latest = None, None
    if backend_commit != "dev":
        backend_latest = await _latest_github_commit_sha("iagentshub/backend_fastapi")
    if frontend_commit != "dev":
        frontend_latest = await _latest_github_commit_sha(frontend_repo)

    commits = {
        "backend_commit": backend_commit,
        "backend_commit_latest": backend_latest[:7] if backend_latest else None,
        "backend_up_to_date": (
            backend_latest.startswith(backend_commit) if backend_latest else None
        ),
        "frontend_commit": frontend_commit,
        "frontend_commit_latest": frontend_latest[:7] if frontend_latest else None,
        "frontend_up_to_date": (
            frontend_latest.startswith(frontend_commit) if frontend_latest else None
        ),
        "frontend_variant": variant,
    }

    if latest_version is None:
        return {
            "checked": False,
            "reason": "no_remote_versions",
            "current_version": current_version,
            **commits,
        }

    return {
        "checked": True,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": latest_version > current_version,
        **commits,
    }


class AutoUpdateUpdate(BaseModel):
    enabled: bool


@admin_router.put("/auto-update")
async def admin_set_auto_update(
    body: AutoUpdateUpdate, _: str = Depends(require_admin)
) -> dict:
    """Arranca/para el contenedor "watchtower" a través de docker-proxy (ver
    docker-compose.hub.yml) y solo si la operación se aplica de verdad
    persiste la preferencia — así el valor guardado nunca miente sobre el
    estado real del contenedor."""
    proxy_url = os.environ.get("DOCKER_PROXY_URL", "")
    if not proxy_url:
        raise APIError(
            409,
            "auto_update_proxy_unavailable",
            "Esta instalación no tiene el proxy de Docker configurado. "
            "Actualiza docker-compose.hub.yml (docker compose pull && "
            "docker compose up -d) para poder controlar la "
            "auto-actualización desde aquí.",
        )
    container = os.environ.get("WATCHTOWER_CONTAINER_NAME", "watchtower")
    action = "start" if body.enabled else "stop"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{proxy_url}/containers/{container}/{action}")
    except httpx.HTTPError as exc:
        raise APIError(
            502,
            "auto_update_apply_failed",
            "No se pudo contactar con el proxy de Docker",
        ) from exc

    # 204 = aplicado; 304 = ya estaba en ese estado (idempotente, también ok).
    if resp.status_code not in (204, 304):
        raise APIError(
            502,
            "auto_update_apply_failed",
            f"Docker rechazó la operación (HTTP {resp.status_code})",
        )

    from app.api.routes.settings import _read_platform_cfg, _write_platform_cfg

    cfg = _read_platform_cfg()
    cfg["auto_update_enabled"] = body.enabled
    _write_platform_cfg(cfg)
    return {"auto_update_enabled": body.enabled}


@admin_router.get("/metadata/tables")
async def admin_metadata_tables(_: str = Depends(require_admin)) -> list:
    """Metadatos de las tablas: nombre, filas, columnas y tamaño estimado."""
    async with open_db() as conn:
        if IS_PG:
            rows = await conn.fetchall("""
                SELECT tablename AS name,
                       (SELECT COUNT(*) FROM information_schema.columns
                        WHERE table_name=tablename AND table_schema='public') AS col_count,
                       COALESCE(n_live_tup, 0) AS rows,
                       pg_total_relation_size(quote_ident(tablename)) AS size_bytes
                FROM   pg_stat_user_tables ORDER BY n_live_tup DESC
            """)
            return [
                {
                    "name": r["name"],
                    "rows": r["rows"],
                    "col_count": r["col_count"],
                    "size_bytes": r["size_bytes"],
                }
                for r in rows
            ]
        else:
            tables = await conn.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            result = []
            for t in tables:
                name = t[0]
                cnt = await conn.fetchval(f'SELECT COUNT(*) FROM "{name}"')
                cols = await conn.fetchall(f'PRAGMA table_info("{name}")')
                try:
                    sz = await conn.fetchval(
                        "SELECT SUM(payload) FROM dbstat WHERE name=?", (name,)
                    )
                except Exception:  # noqa: BLE001 — dbstat puede no estar compilado
                    sz = None
                result.append(
                    {
                        "name": name,
                        "rows": cnt or 0,
                        "col_count": len(cols),
                        "size_bytes": sz,
                    }
                )
            return result


_HIDDEN_COLS = frozenset(
    {
        "password_hash",
        "token",
        "reset_token",
        "verification_token",
        "deletion_token",
        "jwt_secret",
        "stripe_secret_key",
    }
)


@admin_router.get("/metadata/tables/{table_name}/data")
async def admin_metadata_table_data(
    table_name: str,
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: str = Depends(require_admin),
) -> dict:
    """Datos paginados de una tabla. Columnas sensibles enmascaradas."""
    async with open_db() as conn:
        valid = {
            r[0]
            for r in await conn.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
                if not IS_PG
                else "SELECT tablename FROM pg_stat_user_tables"
            )
        }
        if table_name not in valid:
            raise APIError(404, "not_found", "Tabla no encontrada", extra={"resource": "table"})

        if IS_PG:
            col_rows = await conn.fetchall(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=? AND table_schema='public' ORDER BY ordinal_position",
                (table_name,),
            )
            col_names = [r[0] for r in col_rows]
        else:
            col_rows = await conn.fetchall(f'PRAGMA table_info("{table_name}")')
            col_names = [r[1] for r in col_rows]

        if not col_names:
            raise APIError(404, "table_no_columns", "Sin columnas")

        if q:
            cast = "::text" if IS_PG else ""
            clauses = [f'CAST("{c}"{cast} AS TEXT) LIKE ?' for c in col_names]
            where = "WHERE " + " OR ".join(clauses)
            params = [f"%{q}%"] * len(col_names)
        else:
            where, params = "", []

        total = await conn.fetchval(
            f'SELECT COUNT(*) FROM "{table_name}" {where}', tuple(params)
        )
        offset = (page - 1) * page_size
        rows = await conn.fetchall(
            f'SELECT * FROM "{table_name}" {where} LIMIT ? OFFSET ?',
            tuple(params + [page_size, offset]),
        )

        exposed = [c for c in col_names if c not in _HIDDEN_COLS]
        idx_map = [col_names.index(c) for c in exposed]
        data_rows = [
            [
                "[oculto]"
                if col_names[i] in _HIDDEN_COLS
                else (str(row[i]) if row[i] is not None else None)
                for i in idx_map
            ]
            for row in rows
        ]
        pages = (total + page_size - 1) // page_size if total else 0
        return {
            "columns": exposed,
            "rows": data_rows,
            "total": total or 0,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }


@admin_router.get("/stats")
async def admin_stats(_: str = Depends(require_admin)) -> dict[str, Any]:
    import datetime as _dt

    async with open_db() as conn:
        u = await conn.fetchone(
            "SELECT COUNT(*), SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN is_verified=1 THEN 1 ELSE 0 END) FROM users"
        )
        users_total, users_active, users_verified = (u[0] or 0, u[1] or 0, u[2] or 0)

        c = await conn.fetchone(
            "SELECT COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0) FROM connections"
        )
        conns_total, tokens_in, tokens_out = (c[0] or 0, c[1] or 0, c[2] or 0)

        knowledge_total = (
            await conn.fetchval("SELECT COUNT(*) FROM knowledge_items")
        ) or 0
        conversations_total = (
            await conn.fetchval("SELECT COUNT(*) FROM conversations")
        ) or 0
        workflows_total = (
            await conn.fetchval("SELECT COUNT(*) FROM agent_workflows")
        ) or 0

        _today_utc = _dt.datetime.now(_dt.timezone.utc).date()
        cutoff = (_today_utc - _dt.timedelta(days=13)).isoformat()
        today = _today_utc.isoformat()
        try:
            daily_rows = await conn.fetchall(
                "SELECT day, SUM(tokens) FROM token_daily WHERE day >= ? GROUP BY day ORDER BY day ASC",
                (cutoff,),
            )
            tokens_daily = [{"day": r[0], "tokens": r[1]} for r in daily_rows]
            # First-run backfill: seed today from cumulative connection totals
            if not tokens_daily and (tokens_in + tokens_out) > 0:
                if IS_PG:
                    await conn.execute(
                        "INSERT INTO token_daily (day, owner_id, tokens) "
                        "SELECT ?, owner_id, tokens_in + tokens_out FROM connections "
                        "WHERE tokens_in + tokens_out > 0 ON CONFLICT (day, owner_id) DO NOTHING",
                        (today,),
                    )
                else:
                    await conn.execute(
                        "INSERT OR IGNORE INTO token_daily (day, owner_id, tokens) "
                        "SELECT ?, owner_id, tokens_in + tokens_out FROM connections "
                        "WHERE tokens_in + tokens_out > 0",
                        (today,),
                    )
                await conn.commit()
                daily_rows = await conn.fetchall(
                    "SELECT day, SUM(tokens) FROM token_daily WHERE day >= ? GROUP BY day ORDER BY day ASC",
                    (cutoff,),
                )
                tokens_daily = [{"day": r[0], "tokens": r[1]} for r in daily_rows]
        except Exception:  # noqa: BLE001 — backfill best-effort, no debe romper /admin/stats
            tokens_daily = []

    agents_public = (
        len(list(_AGENTS_DIR.glob("public/*/config.json")))
        if _AGENTS_DIR.exists()
        else 0
    )
    agents_private = (
        len(list(_AGENTS_DIR.glob("private/*/config.json")))
        if _AGENTS_DIR.exists()
        else 0
    )

    from app.config.session import WEBMAIL_URL

    return {
        "users_total": users_total,
        "users_active": users_active,
        "users_verified": users_verified,
        "connections_total": conns_total,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "knowledge_total": knowledge_total,
        "conversations_total": conversations_total,
        "workflows_total": workflows_total,
        "agents_public": agents_public,
        "agents_private": agents_private,
        "webmail_url": WEBMAIL_URL,
        "tokens_daily": tokens_daily,
    }


@admin_router.get("/users")
async def admin_list_users(
    q: str | None = None,
    role: str | None = None,
    active: str | None = None,
    verified: str | None = None,
    _: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    users = await list_users()
    async with open_db() as conn:
        token_rows = await conn.fetchall(
            "SELECT owner_id, COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM connections GROUP BY owner_id"
        )
    token_map = {r[0]: {"tokens_in": r[1], "tokens_out": r[2]} for r in token_rows}
    for u in users:
        uname = u.get("username")
        tokens = token_map.get(uname, {"tokens_in": 0, "tokens_out": 0})
        u["tokens_in"] = tokens["tokens_in"]
        u["tokens_out"] = tokens["tokens_out"]
    if q:
        q_low = q.lower()
        users = [
            u
            for u in users
            if q_low in (u.get("username") or "").lower()
            or q_low in (u.get("email") or "").lower()
        ]
    if role:
        users = [u for u in users if u.get("role") == role]
    if active is not None:
        want = active.lower() in ("1", "true", "yes")
        users = [u for u in users if bool(u.get("is_active", 1)) == want]
    if verified is not None:
        want = verified.lower() in ("1", "true", "yes")
        users = [u for u in users if bool(u.get("is_verified", 1)) == want]
    return users


@admin_router.patch("/users/{username}")
async def admin_patch_user(
    username: str,
    request: Request,
    admin: str = Depends(require_admin),
) -> dict[str, Any]:
    if username == admin:
        raise APIError(400, "cannot_modify_own_account", "No puedes modificar tu propia cuenta")
    body = await request.json()
    updates: dict[str, Any] = {}
    if "is_active" in body:
        updates["is_active"] = 1 if body["is_active"] else 0
    if "role" in body:
        if body["role"] not in ("admin", "gestor", "standard"):
            raise APIError(400, "invalid_field", "Rol inválido", extra={"field": "role"})
        updates["role"] = body["role"]
    new_pw = str(body.get("password") or "").strip()
    if new_pw and len(new_pw) < 8:  # N4: mínimo coherente con el registro
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )
    if not updates and not new_pw:
        raise APIError(400, "no_changes", "Sin cambios")
    if updates and not await admin_update_user(username, **updates):
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    if new_pw and not await admin_set_password(username, new_pw):
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    if "is_active" in updates:
        user = await get_user_by_username(username)
        email = user.get("email") if user else None
        if email:
            base_url = _public_base_url(request)
            send_account_status_email(email, bool(updates["is_active"]), base_url)
    return {"ok": True}


@admin_router.post("/users")
async def admin_create_user(
    request: Request,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Crea un usuario directamente desde el panel de admin.

    El usuario queda verificado y activo. No se envía email de verificación.
    """
    from datetime import datetime
    from datetime import timezone as _tz

    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "").strip()
    role = str(body.get("role") or "standard").strip()
    display_name = str(body.get("display_name") or "").strip()

    if not email:
        raise APIError(400, "email_required", "El email es obligatorio")
    if not _EMAIL_RE.match(email):  # N1: misma regex estricta que en /register
        raise APIError(400, "invalid_field", "Email no válido", extra={"field": "email"})
    if not password:
        raise APIError(400, "password_required", "La contraseña es obligatoria")
    if len(password) < 8:  # N4: mínimo coherente con el registro
        raise APIError(400, "password_too_short", "La contraseña debe tener al menos 8 caracteres")
    if role not in ("standard", "admin"):
        raise APIError(
            422, "invalid_field", "role debe ser 'standard' o 'admin'",
            extra={"field": "role"},
        )

    username = email  # username = email (igual que en el registro normal)
    now = datetime.now(_tz.utc).isoformat()
    try:
        async with open_db() as conn, conn.transaction():
            if await conn.fetchone("SELECT 1 FROM users WHERE email = ?", (email,)):
                raise APIError(
                    409, "already_exists", "El email ya está registrado",
                    extra={"resource": "email"},
                )
            if await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (username,)):
                raise APIError(
                    409, "already_exists", "El usuario ya existe",
                    extra={"resource": "user"},
                )
            await conn.execute(
                "INSERT INTO users "
                "(username, email, password_hash, display_name, role, "
                "is_active, is_verified, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    email,
                    hash_password(password),
                    display_name or None,
                    role,
                    1,
                    1,   # verificado — el admin crea la cuenta directamente
                    now,
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise APIError(500, "internal_error", "Error interno del servidor.") from exc

    flog.ok(f"Admin creó usuario: {email} (rol={role})")
    return {"ok": True, "username": username, "email": email, "role": role}


@admin_router.delete("/users/{username}")
async def admin_delete_user(
    username: str, admin: str = Depends(require_admin)
) -> dict[str, Any]:
    if username == admin:
        raise APIError(400, "cannot_delete_own_account", "No puedes eliminar tu propia cuenta")
    if not await delete_user(username):
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    return {"ok": True}


@admin_router.get("/connections")
async def admin_list_connections(
    _: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    import json as _json

    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT id, owner_id, data, tokens_in, tokens_out, created_at "
            "FROM connections ORDER BY created_at DESC"
        )
        email_rows = await conn.fetchall("SELECT username, email FROM users")
    email_map = {r[0]: r[1] for r in email_rows}
    result = []
    for row in rows:
        d = (
            dict(row)
            if isinstance(row, dict)
            else {
                "id": row[0],
                "owner_id": row[1],
                "data": row[2],
                "tokens_in": row[3],
                "tokens_out": row[4],
                "created_at": row[5],
            }
        )
        try:
            data = _json.loads(d.get("data") or "{}")
        except (_json.JSONDecodeError, TypeError):
            data = {}
        result.append(
            {
                "id": d["id"],
                "owner_id": d["owner_id"],
                "owner_email": email_map.get(d["owner_id"], d["owner_id"]),
                "name": data.get("name", d["id"]),
                "type": data.get("type", ""),
                "tokens_in": d["tokens_in"],
                "tokens_out": d["tokens_out"],
                "created_at": d["created_at"],
            }
        )
    return result


@admin_router.delete("/connections/{conn_id}")
async def admin_delete_connection(
    conn_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.config.data import DB_FILE
    from app.storage.storage import ConnectionStorage

    if not await ConnectionStorage(DB_FILE).delete(conn_id, owner_id=None):
        raise APIError(404, "not_found", "Conexión no encontrada", extra={"resource": "connection"})
    return {"ok": True}


@admin_router.get("/agents")
async def admin_list_agents(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    agents = await _agents.list(scope="all")
    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT username, email FROM users")
        conn_rows = await conn.fetchall(
            "SELECT id, owner_id, tokens_in, tokens_out FROM connections"
        )
    email_map = {r[0]: r[1] for r in user_rows}
    conn_data = {
        r[0]: {"owner_id": r[1], "tokens_in": r[2], "tokens_out": r[3]}
        for r in conn_rows
    }
    for a in agents:
        conn_id = a.get("connection_id")
        owner = a.get("owner_id")
        if not owner and conn_id and conn_id in conn_data:
            owner = conn_data[conn_id]["owner_id"]
        a["owner_email"] = email_map.get(owner, owner) if owner else None
        # Agregar tokens de la conexión asociada
        if conn_id and conn_id in conn_data:
            a["tokens_in"] = conn_data[conn_id]["tokens_in"]
            a["tokens_out"] = conn_data[conn_id]["tokens_out"]
        else:
            a["tokens_in"] = 0
            a["tokens_out"] = 0
    return agents


@admin_router.put("/agents/{agent_id}")
async def admin_update_agent(
    agent_id: str,
    request: Request,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    agent = await _agents.get(agent_id, scope="private")
    if not agent:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
    payload = await request.json()
    protected = {"id", "owner_id", "created_at", "scope"}
    updated = {**agent, **{k: v for k, v in payload.items() if k not in protected}}
    new_name = str(updated.get("name") or "").strip()
    if not new_name:
        raise APIError(400, "agent_name_required", "El nombre es obligatorio")
    new_id = re.sub(r"[^a-z0-9_\-]", "-", new_name.lower()).strip("-")
    if new_id != agent_id:
        await _agents.delete(agent_id, scope="private")
    return await _agents.save(updated, "private", owner_id=agent.get("owner_id"))


@admin_router.delete("/agents/{agent_id}")
async def admin_delete_agent(
    agent_id: str,
    scope: str = "private",
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    if scope not in ("public", "private"):
        raise APIError(
            400, "invalid_field", "scope debe ser 'public' o 'private'",
            extra={"field": "scope"},
        )
    deleted = await _agents.delete(agent_id, scope=scope, allow_public=True)
    if not deleted:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
    return {"ok": True}


@admin_router.get("/knowledge")
async def admin_list_knowledge(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    from app.config.data import DB_FILE
    from app.storage.knowledge import KnowledgeStorage

    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT username, email FROM users")
    email_map = {r[0]: r[1] for r in user_rows}
    items = await KnowledgeStorage(DB_FILE).list(owner_id=None)
    for item in items:
        item["owner_email"] = email_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        item.pop("content", None)
    return items


@admin_router.delete("/knowledge/{item_id}")
async def admin_delete_knowledge(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.config.data import DB_FILE
    from app.storage.knowledge import KnowledgeStorage

    if not await KnowledgeStorage(DB_FILE).delete(item_id, owner_id=None):
        raise APIError(404, "not_found", "Elemento no encontrado", extra={"resource": "item"})
    return {"ok": True}


@admin_router.get("/workflows")
async def admin_list_workflows(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    items = await _workflows.list_all()
    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT username, email FROM users")
    email_map = {r[0]: r[1] for r in user_rows}
    for item in items:
        item["owner_email"] = email_map.get(item.get("owner_id", ""), item.get("owner_id", ""))
        definition = item.pop("definition", None) or {}
        item["steps"] = len(definition.get("nodes") or [])
    return items


@admin_router.delete("/workflows/{workflow_id}")
async def admin_delete_workflow(
    workflow_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    if not await _workflows.delete_any(workflow_id):
        raise APIError(404, "not_found", "Orquestación no encontrada", extra={"resource": "workflow"})
    return {"ok": True}


@admin_router.get("/groups")
async def admin_list_groups(
    _: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    import contextlib
    import json as _json

    from app.config.data import AGENTS_DIR

    async with open_db() as conn:
        group_rows = await conn.fetchall(
            "SELECT id, name, created_by, created_at, status FROM groups ORDER BY created_at DESC"
        )
        groups = [
            {"id": r[0], "name": r[1], "created_by": r[2], "created_at": r[3], "status": r[4]}
            for r in group_rows
        ]
        mc_rows = await conn.fetchall(
            "SELECT group_id, COUNT(*) FROM group_members GROUP BY group_id"
        )
        member_counts = {r[0]: r[1] for r in mc_rows}
        cs_rows = await conn.fetchall(
            "SELECT owner_id, COUNT(*), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM connections GROUP BY owner_id"
        )
        conn_stats = {
            r[0]: {"count": r[1], "tokens_in": r[2], "tokens_out": r[3]}
            for r in cs_rows
        }
        kc_rows = await conn.fetchall(
            "SELECT owner_id, COUNT(*) FROM knowledge_items GROUP BY owner_id"
        )
        know_counts = {r[0]: r[1] for r in kc_rows}

    agent_counts: dict[str, int] = {}
    if AGENTS_DIR.exists():
        for cfg_path in AGENTS_DIR.glob("private/*/config.json"):
            with contextlib.suppress(OSError, _json.JSONDecodeError, AttributeError):
                data = _json.loads(cfg_path.read_text())
                owner = data.get("owner_id")
                if owner:
                    agent_counts[owner] = agent_counts.get(owner, 0) + 1

    result = []
    for group in groups:
        group_id = group["id"]
        stats = conn_stats.get(group_id, {"count": 0, "tokens_in": 0, "tokens_out": 0})
        result.append(
            {
                **group,
                "member_count": member_counts.get(group_id, 0),
                "connections_count": stats["count"],
                "tokens_in": stats["tokens_in"],
                "tokens_out": stats["tokens_out"],
                "knowledge_count": know_counts.get(group_id, 0),
                "agents_count": agent_counts.get(group_id, 0),
            }
        )
    return result


@admin_router.delete("/groups/{group_id}")
async def admin_delete_group(
    group_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    if not await _groups.get(group_id):
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    await _groups.delete(group_id)
    return {"ok": True}


@admin_router.post("/groups/{group_id}/status")
async def admin_set_group_status(
    group_id: str, request: Request, _: str = Depends(require_admin)
) -> dict[str, Any]:
    body = await request.json()
    status = str(body.get("status") or "").strip()
    if status not in ("active", "disabled"):
        raise APIError(
            422, "invalid_field", "status debe ser 'active' o 'disabled'",
            extra={"field": "status"},
        )
    if not await _groups.get(group_id):
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    await _groups.set_status(group_id, status)
    return {"ok": True, "status": status}


@admin_router.put("/resources/{resource_type}/{resource_id}/verify")
async def admin_verify_resource(
    resource_type: str,
    resource_id: str,
    request: Request,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    _valid_types = ("agent", "skill", "knowledge")
    if resource_type not in _valid_types:
        raise APIError(
            422,
            "invalid_field",
            f"resource_type debe ser uno de {_valid_types}",
            extra={"field": "resource_type"},
        )
    body = await request.json()
    verified_val = bool(body.get("verified", False))
    db_val = verified_val if IS_PG else (1 if verified_val else 0)

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT 1 FROM resource_social WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        )
        if not row:
            raise APIError(
                404, "not_found", "Recurso no encontrado en el catálogo social",
                extra={"resource": "resource"},
            )
        await conn.execute(
            "UPDATE resource_social SET verified=? WHERE resource_type=? AND resource_id=?",
            (db_val, resource_type, resource_id),
        )
        await conn.commit()
    return {"ok": True}


@admin_router.put("/resources/{resource_type}/{resource_id}/owner")
async def admin_set_resource_owner(
    resource_type: str,
    resource_id: str,
    request: Request,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Reasigna el propietario de un recurso a otro usuario existente."""
    table_map = {
        "agent": "agents",
        "skill": "skills",
        "connection": "connections",
        "knowledge": "knowledge_items",
        "workflow": "agent_workflows",
    }
    table = table_map.get(resource_type)
    if not table:
        raise APIError(
            422,
            "invalid_field",
            f"resource_type debe ser uno de {list(table_map)}",
            extra={"field": "resource_type"},
        )
    body = await request.json()
    new_owner = str(body.get("owner_id") or "").strip()
    if not new_owner:
        raise APIError(400, "invalid_field", "owner_id es obligatorio", extra={"field": "owner_id"})

    async with open_db() as conn:
        user_row = await conn.fetchone(
            "SELECT username, is_active FROM users WHERE username=?", (new_owner,)
        )
        if not user_row:
            raise APIError(
                404, "not_found", "El usuario propietario no existe",
                extra={"resource": "user"},
            )
        if not user_row["is_active"]:
            raise APIError(
                400, "invalid_field", "El usuario propietario no está activo",
                extra={"field": "owner_id"},
            )
        row = await conn.fetchone(f"SELECT id FROM {table} WHERE id=?", (resource_id,))
        if not row:
            raise APIError(
                404, "not_found", "Recurso no encontrado", extra={"resource": resource_type},
            )
        await conn.execute(f"UPDATE {table} SET owner_id=? WHERE id=?", (new_owner, resource_id))
        await conn.commit()
    return {"ok": True}


@admin_router.post("/impersonate/{username}")
async def admin_impersonate(
    username: str,
    response: Response,
    admin: str = Depends(require_admin),
) -> dict[str, Any]:
    if username == admin:
        raise APIError(400, "already_own_user", "Ya eres este usuario")

    target_user = await get_user_by_username(username)
    if not target_user:
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})

    # Verificar que la cuenta del usuario objetivo esté activa
    if not target_user.get("is_active", 1):
        raise APIError(
            400, "cannot_impersonate_disabled", "No se puede impersonar una cuenta desactivada"
        )

    # N3: registrar la impersonación para auditoría de seguridad
    flog.warning(f"[admin] IMPERSONACIÓN: admin={admin!r} → usuario={username!r}")

    # Crear token para el group personal del usuario impersonado
    # (group_id=username por defecto)
    token = create_token(username)

    # Establecer la cookie del nuevo token
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )

    flog.ok(f"[admin] Token de impersonación creado exitosamente para {username!r}")
    return {"ok": True, "username": username}
