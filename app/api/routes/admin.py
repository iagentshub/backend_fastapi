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

import asyncio
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import BaseModel

from app.api.routes.auth import _public_base_url, require_admin
from app.auth.auth import (
    admin_set_password,
    admin_update_user,
    create_token,
    delete_user,
    get_user_by_username,
    hash_password_async,
    list_users,
)
from app.config.data import AGENTS_DIR as _AGENTS_DIR
from app.config.data import DB_FILE as _DB_FILE
from app.config.session import SECURE_COOKIES
from app.errors import APIError
from app.services.email import send_account_status_email
from app.storage.db import IS_PG, open_db
from app.storage.groups import GroupStorage as _GroupStorage
from app.storage.storage import AgentStorage as _AgentStorage
from app.storage.workflows import WorkflowStorage as _WorkflowStorage
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.validation import is_valid_email, is_valid_username, normalize_username

_groups = _GroupStorage(_DB_FILE)
_agents = _AgentStorage(_AGENTS_DIR)
_workflows = _WorkflowStorage()

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

_ADMIN_EXPLORE_TYPES = (
    "user",
    "group",
    "agent",
    "connection",
    "knowledge",
    "workflow",
    "skill",
    "memory",
)


def _version_re(variant: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(variant)}-(\d{{14}})$")


async def _latest_ghcr_version(image: str, variant: str) -> str | None:
    """Versión (tag `<variant>-YYYYMMDDHHMMSS`) más reciente publicada en
    GHCR para `image`, restringida a la familia indicada.

    El prefijo evita mezclar tags históricos o ajenos con las versiones React
    soportadas actualmente.
    """
    prefix = "ghcr.io/"
    if not image.startswith(prefix):
        raise APIError(
            500,
            "invalid_field",
            "IMAGE_REPOSITORY debe apuntar a ghcr.io",
            extra={"field": "IMAGE_REPOSITORY"},
        )
    repository = image.removeprefix(prefix).strip("/")
    pattern = _version_re(variant)
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.get(
            "https://ghcr.io/token",
            params={
                "service": "ghcr.io",
                "scope": f"repository:{repository}:pull",
            },
        )
        token_response.raise_for_status()
        token = str(token_response.json().get("token") or "")
        if not token:
            raise httpx.HTTPError("GHCR no devolvió un token de lectura")
        tags: list[str] = []
        tags_url: str | None = f"https://ghcr.io/v2/{repository}/tags/list?n=1000"
        for _ in range(10):
            if tags_url is None:
                break
            tags_response = await client.get(
                tags_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            tags_response.raise_for_status()
            tags.extend(tags_response.json().get("tags") or [])
            link = tags_response.headers.get("link", "")
            next_link = re.search(r'<([^>]+)>;\s*rel="next"', link)
            tags_url = next_link.group(1) if next_link else None
            if tags_url and tags_url.startswith("/"):
                tags_url = f"https://ghcr.io{tags_url}"
    versions = [
        match.group(1) for tag in tags if (match := pattern.match(str(tag))) is not None
    ]
    return max(versions) if versions else None


async def _latest_github_commit_sha(repo: str, branch: str = "main") -> str | None:
    """SHA completo del último commit en `branch` de `repo` (API pública de
    GitHub, sin autenticación). None si la consulta falla por cualquier motivo
    — a diferencia de GHCR, esta comprobación es solo informativa
    (distingue si lo desactualizado es el backend o el frontend) y nunca debe
    tumbar el resto de /check-update.
    """
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url, headers={"Accept": "application/vnd.github+json"}
            )
            resp.raise_for_status()
            return resp.json().get("sha")
    except httpx.HTTPError:
        return None


@admin_router.get("/check-update")
async def admin_check_update(_: str = Depends(require_admin)) -> dict:
    """Compara la versión de la imagen en ejecución (GAIA_VERSION) contra la
    más reciente publicada en GHCR. Solo informa — no aplica el update
    (eso lo hace Watchtower, o `docker compose pull && up -d` manual).

    GAIA_VERSION solo dice "cuándo" se construyó la imagen, no "qué" código de
    backend_fastapi, frontend_react ni app_flutter lleva dentro — así que
    además se compara el commit horneado de cada uno de los tres repos
    (BACKEND_COMMIT/FRONTEND_COMMIT/APP_COMMIT) contra el HEAD de main en
    GitHub, para saber cuál está desactualizado.
    """
    current_version = os.environ.get("GAIA_VERSION", "dev")
    if current_version == "dev":
        return {
            "checked": False,
            "reason": "no_version",
            "current_version": current_version,
        }

    image_repository = os.environ.get("IMAGE_REPOSITORY", "ghcr.io/iagentshub/app")
    image_variant = os.environ.get("IMAGE_VARIANT", "react")
    try:
        latest_version = await _latest_ghcr_version(image_repository, image_variant)
    except httpx.HTTPError as exc:
        raise APIError(502, "check_update_failed", "No se pudo consultar GHCR") from exc

    backend_commit = os.environ.get("BACKEND_COMMIT", "dev")
    frontend_commit = os.environ.get("FRONTEND_COMMIT", "dev")
    app_commit = os.environ.get("APP_COMMIT", "dev")
    frontend_repo = "iagentshub/frontend_react"
    app_repo = "iagentshub/app_flutter"
    backend_latest, frontend_latest, app_latest = None, None, None
    if backend_commit != "dev":
        backend_latest = await _latest_github_commit_sha("iagentshub/backend_fastapi")
    if frontend_commit != "dev":
        frontend_latest = await _latest_github_commit_sha(frontend_repo)
    if app_commit != "dev":
        app_latest = await _latest_github_commit_sha(app_repo)

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
        "app_commit": app_commit,
        "app_commit_latest": app_latest[:7] if app_latest else None,
        "app_up_to_date": (app_latest.startswith(app_commit) if app_latest else None),
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


async def _trigger_watchtower_update(token: str) -> None:
    """Si Watchtower encuentra una imagen nueva, sustituye ESTE MISMO
    contenedor a mitad de la petición — por eso se dispara en segundo plano
    en vez de esperar su respuesta desde el endpoint: perder la conexión aquí
    es la señal esperada de que se está aplicando, no un fallo real."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            await client.post(
                "http://watchtower:8080/v1/update",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        pass


@admin_router.post("/update-now")
async def admin_update_now(
    background_tasks: BackgroundTasks, _: str = Depends(require_admin)
) -> dict:
    """Fuerza ahora mismo el ciclo de comprobación+actualización que
    Watchtower ejecuta cada WATCHTOWER_INTERVAL segundos, vía su propia API
    HTTP (WATCHTOWER_HTTP_API_UPDATE en docker-compose.hub.yml) — sin ampliar
    los permisos de docker-proxy, deliberadamente restringidos a start/stop
    (ver ese fichero para el razonamiento).
    """
    token = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "")
    if not token:
        raise APIError(
            409,
            "update_now_unavailable",
            "Esta instalación no tiene la API HTTP de Watchtower activada. "
            "Actualiza docker-compose.hub.yml y vuelve a desplegar (docker "
            "compose pull && docker compose up -d) para poder disparar "
            "actualizaciones desde aquí.",
        )
    background_tasks.add_task(_trigger_watchtower_update, token)
    return {"triggered": True}


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
            raise APIError(
                404, "not_found", "Tabla no encontrada", extra={"resource": "table"}
            )

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


def _server_health() -> dict[str, Any]:
    """Disco/memoria/CPU del host — sin dependencias nuevas (evita psutil):
    shutil.disk_usage es stdlib, la memoria se lee de /proc/meminfo (Linux,
    válido en el contenedor de producción) y la CPU se aproxima con la load
    average normalizada por núcleos. Si algo no está disponible (p.ej. correr
    en macOS en local) se devuelve None en ese campo en vez de romper /stats
    entero — este dato es informativo, nunca debe tumbar el panel de admin."""
    import shutil as _shutil

    from app.config.data import DATA_DIR as _DATA_DIR

    health: dict[str, Any] = {
        "disk_used_pct": None,
        "disk_used_gb": None,
        "disk_total_gb": None,
        "memory_used_pct": None,
        "memory_used_gb": None,
        "memory_total_gb": None,
        "cpu_load_pct": None,
        "cpu_cores": None,
    }
    try:
        usage = _shutil.disk_usage(_DATA_DIR)
        health["disk_used_pct"] = round(usage.used / usage.total * 100, 1)
        health["disk_used_gb"] = round(usage.used / 1_073_741_824, 1)
        health["disk_total_gb"] = round(usage.total / 1_073_741_824, 1)
    except Exception:  # noqa: BLE001 — informativo, no debe romper /stats
        pass

    try:
        meminfo: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.strip().split()[0])  # kB
        mem_total = meminfo.get("MemTotal", 0)
        mem_available = meminfo.get("MemAvailable", 0)
        if mem_total:
            mem_used = mem_total - mem_available
            health["memory_used_pct"] = round(mem_used / mem_total * 100, 1)
            health["memory_used_gb"] = round(mem_used / 1_048_576, 1)
            health["memory_total_gb"] = round(mem_total / 1_048_576, 1)
    except Exception:  # noqa: BLE001 — /proc/meminfo no existe fuera de Linux
        pass

    try:
        cores = os.cpu_count() or 1
        load_1min = os.getloadavg()[0]
        health["cpu_cores"] = cores
        health["cpu_load_pct"] = round(load_1min / cores * 100, 1)
    except (OSError, AttributeError):
        pass

    return health


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

        # "date" en app_logs se escribe con datetime.now() local (ver
        # app/utils/flog.py) — se usa la misma convención aquí para que
        # "hoy" coincida con lo ya persistido, en vez de la fecha UTC que
        # usa tokens_daily arriba.
        today_local = _dt.datetime.now().strftime("%Y-%m-%d")
        log_rows = await conn.fetchall(
            "SELECT level, summary FROM app_logs WHERE source='BE' AND date = ?",
            (today_local,),
        )

    requests_today = len(log_rows)
    errors_today = 0
    endpoint_error_counts: dict[str, int] = {}
    latency_total = 0
    latency_count = 0
    _latency_re = re.compile(r"\((\d+)ms\)\s*$")
    for level, summary in log_rows:
        match = _latency_re.search(summary)
        if match:
            latency_total += int(match.group(1))
            latency_count += 1
        if level == "ERROR":
            errors_today += 1
            endpoint = summary.split(" → ", 1)[0].strip()
            endpoint_error_counts[endpoint] = endpoint_error_counts.get(endpoint, 0) + 1

    failure_rate_pct = (
        round(errors_today / requests_today * 100, 1) if requests_today else 0.0
    )
    avg_latency_ms = round(latency_total / latency_count) if latency_count else 0
    top_error_endpoint, top_error_count = (
        max(endpoint_error_counts.items(), key=lambda kv: kv[1])
        if endpoint_error_counts
        else (None, 0)
    )

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
        "requests_today": requests_today,
        "errors_today": errors_today,
        "failure_rate_pct": failure_rate_pct,
        "avg_latency_ms": avg_latency_ms,
        "top_error_endpoint": top_error_endpoint,
        "top_error_count": top_error_count,
        **_server_health(),
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
        tokens = token_map.get(u.get("id"), {"tokens_in": 0, "tokens_out": 0})
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
    target = await get_user_by_username(username)
    if target and target["id"] == admin:
        raise APIError(
            400, "cannot_modify_own_account", "No puedes modificar tu propia cuenta"
        )
    body = await request.json()
    updates: dict[str, Any] = {}
    if "is_active" in body:
        updates["is_active"] = 1 if body["is_active"] else 0
    if "role" in body:
        if body["role"] not in ("admin", "gestor", "standard"):
            raise APIError(
                400, "invalid_field", "Rol inválido", extra={"field": "role"}
            )
        updates["role"] = body["role"]
    new_pw = str(body.get("password") or "").strip()
    if new_pw and len(new_pw) < 8:  # N4: mínimo coherente con el registro
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )
    if not updates and not new_pw:
        raise APIError(400, "no_changes", "Sin cambios")
    if updates and not await admin_update_user(username, **updates):
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    if new_pw and not await admin_set_password(username, new_pw):
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
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
    username = normalize_username(str(body.get("username") or ""))
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "").strip()
    role = str(body.get("role") or "standard").strip()
    display_name = str(body.get("display_name") or "").strip()

    if not is_valid_username(username):
        raise APIError(
            400,
            "invalid_field",
            "El usuario debe tener entre 5 y 32 caracteres: a-z, 0-9, punto, guion o guion bajo",
            extra={"field": "username"},
        )
    if not email:
        raise APIError(400, "email_required", "El email es obligatorio")
    if not is_valid_email(email):
        raise APIError(
            400, "invalid_field", "Email no válido", extra={"field": "email"}
        )
    if not password:
        raise APIError(400, "password_required", "La contraseña es obligatoria")
    if len(password) < 8:  # N4: mínimo coherente con el registro
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )
    if role not in ("standard", "admin"):
        raise APIError(
            422,
            "invalid_field",
            "role debe ser 'standard' o 'admin'",
            extra={"field": "role"},
        )

    now = datetime.now(_tz.utc).isoformat()
    password_hash = await hash_password_async(password)
    try:
        async with open_db() as conn, conn.transaction():
            if await conn.fetchone("SELECT 1 FROM users WHERE email = ?", (email,)):
                raise APIError(
                    409,
                    "already_exists",
                    "El email ya está registrado",
                    extra={"resource": "email"},
                )
            if await conn.fetchone(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ):
                raise APIError(
                    409,
                    "already_exists",
                    "El usuario ya existe",
                    extra={"resource": "user"},
                )
            await conn.execute(
                "INSERT INTO users "
                "(id, username, email, password_hash, display_name, role, "
                "is_active, is_verified, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    generate_id(32),
                    username,
                    email,
                    password_hash,
                    display_name or None,
                    role,
                    1,
                    1,  # verificado — el admin crea la cuenta directamente
                    now,
                ),
            )
    except APIError:
        raise
    except Exception as exc:
        raise APIError(500, "internal_error", "Error interno del servidor.") from exc

    flog.ok(f"Admin creó usuario: {email} (rol={role})")
    return {"ok": True, "username": username, "email": email, "role": role}


@admin_router.delete("/users/{username}")
async def admin_delete_user(
    username: str, admin: str = Depends(require_admin)
) -> dict[str, Any]:
    target = await get_user_by_username(username)
    if target and target["id"] == admin:
        raise APIError(
            400, "cannot_delete_own_account", "No puedes eliminar tu propia cuenta"
        )
    if not await delete_user(username):
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    return {"ok": True}


@admin_router.get("/connections")
async def admin_list_connections(
    _: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    import json as _json

    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT id, owner_id, name, data, tokens_in, tokens_out, created_at "
            "FROM connections ORDER BY created_at DESC"
        )
        user_rows = await conn.fetchall("SELECT id, username FROM users")
    username_map = {r[0]: r[1] for r in user_rows}
    result = []
    for row in rows:
        d = (
            dict(row)
            if isinstance(row, dict)
            else {
                "id": row[0],
                "owner_id": row[1],
                "name": row[2],
                "data": row[3],
                "tokens_in": row[4],
                "tokens_out": row[5],
                "created_at": row[6],
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
                "owner_username": username_map.get(d["owner_id"], d["owner_id"]),
                "name": d["name"],
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
        raise APIError(
            404, "not_found", "Conexión no encontrada", extra={"resource": "connection"}
        )
    return {"ok": True}


@admin_router.get("/agents")
async def admin_list_agents(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    agents = await _agents.list(scope="all")
    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT id, username FROM users")
        conn_rows = await conn.fetchall(
            "SELECT id, owner_id, tokens_in, tokens_out FROM connections"
        )
    username_map = {r[0]: r[1] for r in user_rows}
    conn_data = {
        r[0]: {"owner_id": r[1], "tokens_in": r[2], "tokens_out": r[3]}
        for r in conn_rows
    }
    for a in agents:
        conn_id = a.get("connection_id")
        owner = a.get("owner_id")
        if not owner and conn_id and conn_id in conn_data:
            owner = conn_data[conn_id]["owner_id"]
        a["owner_username"] = username_map.get(owner, owner) if owner else None
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
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    payload = await request.json()
    protected = {"id", "owner_id", "created_at", "scope"}
    updated = {**agent, **{k: v for k, v in payload.items() if k not in protected}}
    new_name = str(updated.get("name") or "").strip()
    if not new_name:
        raise APIError(400, "agent_name_required", "El nombre es obligatorio")
    return await _agents.save(updated, "private", owner_id=agent.get("owner_id"))


@admin_router.delete("/agents/{agent_id}")
async def admin_delete_agent(
    agent_id: str,
    scope: str = "private",
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    if scope not in ("public", "private"):
        raise APIError(
            400,
            "invalid_field",
            "scope debe ser 'public' o 'private'",
            extra={"field": "scope"},
        )
    deleted = await _agents.delete(agent_id, scope=scope, allow_public=True)
    if not deleted:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    return {"ok": True}


@admin_router.get("/skills")
async def admin_list_skills(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    from app.config.data import SKILLS_DIR
    from app.storage.storage import SkillStorage

    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT id, username FROM users")
    username_map = {r[0]: r[1] for r in user_rows}
    items = await SkillStorage(SKILLS_DIR).list("all")
    for item in items:
        item["owner_username"] = username_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        item.pop("content", None)
    return items


@admin_router.delete("/skills/{item_id}")
async def admin_delete_skill(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.config.data import SKILLS_DIR
    from app.storage.storage import SkillStorage

    storage = SkillStorage(SKILLS_DIR)
    skill = await storage.get_any(item_id)
    if not skill:
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "item"}
        )
    await storage.delete(skill["scope"], item_id, owner_id=None, allow_public=True)
    return {"ok": True}


@admin_router.get("/memory")
async def admin_list_memory(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    """A diferencia del resto de recursos, el nombre de un fichero de memoria
    lo elige el usuario (no un ID generado) y solo es único por dueño (PK
    compuesta (id, owner_id) en memory_files) — dos usuarios pueden tener
    ambos un fichero "notes". Para que Admin (que asume IDs únicas
    globalmente) pueda listar/enlazar/borrar cada uno sin ambigüedad, el
    "id" que se expone aquí es "{owner_id}::{filename}"."""
    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT id, username FROM users")
        username_map = {r[0]: r[1] for r in user_rows}
        rows = await conn.fetchall(
            "SELECT id, owner_id, content, updated_at FROM memory_files "
            "ORDER BY updated_at DESC"
        )
    return [
        {
            "id": f"{r['owner_id']}::{r['id']}",
            "filename": r["id"],
            "owner_id": r["owner_id"],
            "owner_username": username_map.get(r["owner_id"], r["owner_id"]),
            "size": len(r["content"] or ""),
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


@admin_router.delete("/memory/{item_id}")
async def admin_delete_memory(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.config.data import MEMORY_DIR
    from app.storage.storage import MemoryStorage

    owner_id, sep, filename = item_id.partition("::")
    if not sep:
        raise APIError(
            422, "invalid_field", "id de memoria no válido", extra={"field": "item_id"}
        )
    if not await MemoryStorage(MEMORY_DIR).delete(filename, owner_id=owner_id):
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "item"}
        )
    return {"ok": True}


@admin_router.get("/knowledge")
async def admin_list_knowledge(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    from app.config.data import DB_FILE
    from app.storage.knowledge import KnowledgeStorage

    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT id, username FROM users")
    username_map = {r[0]: r[1] for r in user_rows}
    items = await KnowledgeStorage(DB_FILE).list(owner_id=None)
    for item in items:
        item["owner_username"] = username_map.get(
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
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "item"}
        )
    return {"ok": True}


@admin_router.get("/workflows")
async def admin_list_workflows(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    items = await _workflows.list_all()
    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT id, username FROM users")
    username_map = {r[0]: r[1] for r in user_rows}
    for item in items:
        item["owner_username"] = username_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        definition = item.pop("definition", None) or {}
        item["steps"] = len(definition.get("nodes") or [])
    return items


@admin_router.delete("/workflows/{workflow_id}")
async def admin_delete_workflow(
    workflow_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    if not await _workflows.delete_any(workflow_id):
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
        )
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
            "SELECT g.id, g.name, g.created_by, u.username, g.created_at, g.is_active "
            "FROM groups g LEFT JOIN users u ON u.id = g.created_by "
            "ORDER BY g.created_at DESC"
        )
        groups = [
            {
                "id": r[0],
                "name": r[1],
                "created_by": r[2],
                "created_by_username": r[3],
                "created_at": r[4],
                "is_active": bool(r[5]),
                "status": "active" if r[5] else "disabled",
            }
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
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
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
            422,
            "invalid_field",
            "status debe ser 'active' o 'disabled'",
            extra={"field": "status"},
        )
    if not await _groups.get(group_id):
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    await _groups.set_status(group_id, status)
    return {"ok": True, "status": status}


def _explore_search_text(resource_type: str, item: dict[str, Any]) -> str:
    fields = {
        "user": ("username", "email", "display_name"),
        "group": ("name", "created_by_username"),
        "agent": ("name", "id", "owner_username", "description"),
        "connection": ("name", "id", "owner_username", "type"),
        "knowledge": ("title", "id", "owner_username", "type"),
        "workflow": ("name", "id", "owner_username", "description"),
        "skill": ("name", "id", "owner_username", "category"),
        "memory": ("filename", "id", "owner_username"),
    }[resource_type]
    return " ".join(str(item.get(field) or "") for field in fields).lower()


async def _admin_inventory() -> dict[str, list[dict[str, Any]]]:
    (
        users,
        groups,
        agents,
        connections,
        knowledge,
        workflows,
        skills,
        memory,
    ) = await asyncio.gather(
        admin_list_users(_=""),
        admin_list_groups(_=""),
        admin_list_agents(_=""),
        admin_list_connections(_=""),
        admin_list_knowledge(_=""),
        admin_list_workflows(_=""),
        admin_list_skills(_=""),
        admin_list_memory(_=""),
    )
    return {
        "user": users,
        "group": groups,
        "agent": agents,
        "connection": connections,
        "knowledge": knowledge,
        "workflow": workflows,
        "skill": skills,
        "memory": memory,
    }


@admin_router.get("/explore")
async def admin_explore(
    resource_types: list[str] | None = Query(None, alias="type"),
    q: str | None = None,
    owner: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Inventario administrativo unificado con discriminador por tipo."""
    requested = set(resource_types or _ADMIN_EXPLORE_TYPES)
    invalid = requested.difference(_ADMIN_EXPLORE_TYPES)
    if invalid:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de recurso no válido",
            extra={"field": "type"},
        )

    inventory = await _admin_inventory()
    query = (q or "").strip().lower()
    owner_filter = (owner or "").strip().lower()
    counts = {resource_type: len(values) for resource_type, values in inventory.items()}
    items: list[dict[str, Any]] = []
    for resource_type in _ADMIN_EXPLORE_TYPES:
        if resource_type not in requested:
            continue
        for raw in inventory[resource_type]:
            if query and query not in _explore_search_text(resource_type, raw):
                continue
            item_owner = str(
                raw.get("owner_username")
                or raw.get("created_by_username")
                or raw.get("username")
                or ""
            ).lower()
            if owner_filter and item_owner != owner_filter:
                continue
            item = dict(raw)
            item["resource_type"] = resource_type
            items.append(item)

    def sort_key(item: dict[str, Any]) -> str:
        return str(item.get("updated_at") or item.get("created_at") or "")

    items.sort(key=sort_key, reverse=True)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "counts": counts,
        "limit": limit,
        "offset": offset,
    }


def _graph_node(
    resource_type: str,
    resource_id: str,
    label: str,
    description: str = "",
) -> dict[str, str]:
    return {
        "id": f"{resource_type}:{resource_id}",
        "resource_id": resource_id,
        "label": label or resource_id,
        "type": resource_type,
        "description": description,
    }


@admin_router.get("/resources/{resource_type}/{resource_id}/graph")
async def admin_resource_graph(
    resource_type: str,
    resource_id: str,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Devuelve el vecindario relacional inmediato de un recurso de admin."""
    if resource_type not in _ADMIN_EXPLORE_TYPES:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de recurso no válido",
            extra={"field": "resource_type"},
        )

    inventory = await _admin_inventory()
    workflows_full = await _workflows.list_all()
    workflows_by_id = {str(item.get("id")): item for item in workflows_full}
    users_by_id = {str(item.get("id")): item for item in inventory["user"]}
    users_by_username = {str(item.get("username")): item for item in inventory["user"]}
    groups_by_id = {str(item.get("id")): item for item in inventory["group"]}
    resources_by_type = {
        kind: {str(item.get("id")): item for item in values}
        for kind, values in inventory.items()
        if kind not in ("user", "group")
    }

    if resource_type == "user":
        root = users_by_id.get(resource_id) or users_by_username.get(resource_id)
    elif resource_type == "group":
        root = groups_by_id.get(resource_id)
    else:
        root = resources_by_type[resource_type].get(resource_id)
    if not root:
        raise APIError(
            404,
            "not_found",
            "Recurso no encontrado",
            extra={"resource": resource_type},
        )

    canonical_resource_id = str(root.get("id") or resource_id)
    nodes: dict[str, dict[str, str]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_node(kind: str, item_id: str, label: str, description: str = "") -> str:
        node = _graph_node(kind, item_id, label, description)
        nodes[node["id"]] = node
        return node["id"]

    def add_edge(
        source: str, target: str, relation: str, *, dashed: bool = False
    ) -> None:
        edges[(source, target, relation)] = {
            "source_id": source,
            "target_id": target,
            "relation": relation,
            "dashed": dashed,
        }

    def resource_label(kind: str, item: dict[str, Any]) -> str:
        return str(
            item.get("name")
            or item.get("title")
            or item.get("username")
            or item.get("id")
            or kind
        )

    root_id = add_node(
        resource_type,
        canonical_resource_id,
        resource_label(resource_type, root),
        str(root.get("description") or root.get("email") or ""),
    )

    def connect_owner(kind: str, item: dict[str, Any]) -> None:
        owner_id = str(item.get("owner_id") or "")
        if not owner_id:
            return
        if owner_id in users_by_id:
            owner = users_by_id[owner_id]
            owner_node = add_node("user", owner_id, resource_label("user", owner))
        elif owner_id in groups_by_id:
            owner = groups_by_id[owner_id]
            owner_node = add_node("group", owner_id, resource_label("group", owner))
        else:
            return
        item_node = add_node(kind, str(item["id"]), resource_label(kind, item))
        add_edge(owner_node, item_node, "owns")

    if resource_type in resources_by_type:
        connect_owner(resource_type, root)

    def wire_agent_uses(agent: dict[str, Any], agent_node: str) -> None:
        """Añade las aristas "usa" de un agente (conexión/knowledge/skill/
        memoria) — reutilizable tanto cuando el agente es la raíz del grafo
        como cuando aparece como hijo de su usuario/grupo propietario, para
        que esa vista también muestre qué usa cada agente y no solo qué
        posee el usuario en plano."""
        connection_ids = [str(agent.get("connection_id") or "")]
        connection_ids.extend(
            str(value).split("::", 1)[0]
            for value in (agent.get("op_connections") or [])
        )
        for connection_id in {value for value in connection_ids if value}:
            connection = resources_by_type["connection"].get(connection_id)
            if connection:
                connection_node = add_node(
                    "connection",
                    connection_id,
                    resource_label("connection", connection),
                )
                add_edge(agent_node, connection_node, "uses")
        for knowledge_id in agent.get("knowledge") or []:
            knowledge = resources_by_type["knowledge"].get(str(knowledge_id))
            if knowledge:
                knowledge_node = add_node(
                    "knowledge",
                    str(knowledge_id),
                    resource_label("knowledge", knowledge),
                )
                add_edge(agent_node, knowledge_node, "uses")
        for skill_id in agent.get("skills") or []:
            skill = resources_by_type["skill"].get(str(skill_id))
            skill_node = add_node(
                "skill",
                str(skill_id),
                resource_label("skill", skill) if skill else str(skill_id),
            )
            add_edge(agent_node, skill_node, "uses")
        memory_file = str(agent.get("memory_file") or "")
        if agent.get("use_memory") and memory_file:
            # El id de memoria es compuesto ("owner_id::filename", ver
            # admin_list_memory) porque el nombre solo es único por dueño.
            memory_key = f"{str(agent.get('owner_id') or '')}::{memory_file}"
            memory = resources_by_type["memory"].get(memory_key)
            if memory:
                memory_node = add_node("memory", memory_key, memory_file)
                add_edge(agent_node, memory_node, "uses")

    async with open_db() as conn:
        member_rows = await conn.fetchall(
            "SELECT group_id, username, role FROM group_members"
        )
        share_rows = await conn.fetchall(
            "SELECT group_id, resource_type, resource_id FROM resource_group_shares"
        )

    if resource_type == "user":
        username = str(root.get("username") or "")
        user_id = str(root.get("id") or "")
        for row in member_rows:
            if str(row["username"]) != username:
                continue
            group = groups_by_id.get(str(row["group_id"]))
            if group:
                group_node = add_node(
                    "group", str(group["id"]), resource_label("group", group)
                )
                add_edge(root_id, group_node, f"member:{row['role']}")
        for kind, by_id in resources_by_type.items():
            for item in by_id.values():
                if str(item.get("owner_id") or "") == user_id:
                    item_node = add_node(
                        kind, str(item["id"]), resource_label(kind, item)
                    )
                    add_edge(root_id, item_node, "owns")
                    if kind == "agent":
                        wire_agent_uses(item, item_node)

    if resource_type == "group":
        for row in member_rows:
            if str(row["group_id"]) != canonical_resource_id:
                continue
            user = users_by_username.get(str(row["username"]))
            if user:
                user_node = add_node(
                    "user", str(user["id"]), resource_label("user", user)
                )
                add_edge(user_node, root_id, f"member:{row['role']}")
        for kind, by_id in resources_by_type.items():
            for item in by_id.values():
                if str(item.get("owner_id") or "") == canonical_resource_id:
                    item_node = add_node(
                        kind, str(item["id"]), resource_label(kind, item)
                    )
                    add_edge(root_id, item_node, "owns")
                    if kind == "agent":
                        wire_agent_uses(item, item_node)

    for row in share_rows:
        kind = str(row["resource_type"])
        item_id = str(row["resource_id"])
        group_id = str(row["group_id"])
        if kind not in resources_by_type or item_id not in resources_by_type[kind]:
            continue
        if not (
            (resource_type == "group" and canonical_resource_id == group_id)
            or (resource_type == kind and canonical_resource_id == item_id)
        ):
            continue
        group = groups_by_id.get(group_id)
        item = resources_by_type[kind][item_id]
        if group:
            group_node = add_node("group", group_id, resource_label("group", group))
            item_node = add_node(kind, item_id, resource_label(kind, item))
            add_edge(group_node, item_node, "shared", dashed=True)

    agents = resources_by_type["agent"]
    if resource_type == "agent":
        wire_agent_uses(root, root_id)

    if resource_type == "memory":
        memory_owner_id, _, memory_filename = canonical_resource_id.partition("::")
        for agent in agents.values():
            if (
                str(agent.get("owner_id") or "") == memory_owner_id
                and agent.get("use_memory")
                and str(agent.get("memory_file") or "") == memory_filename
            ):
                agent_node = add_node(
                    "agent", str(agent["id"]), resource_label("agent", agent)
                )
                add_edge(agent_node, root_id, "uses")

    if resource_type in ("connection", "knowledge", "skill"):
        field = (
            "connection_id"
            if resource_type == "connection"
            else "knowledge"
            if resource_type == "knowledge"
            else "skills"
        )
        for agent in agents.values():
            if field == "connection_id":
                operation_connections = {
                    str(value).split("::", 1)[0]
                    for value in (agent.get("op_connections") or [])
                }
                related = (
                    str(agent.get(field) or "") == canonical_resource_id
                    or canonical_resource_id in operation_connections
                )
            else:
                related = canonical_resource_id in {
                    str(value) for value in (agent.get(field) or [])
                }
            if related:
                agent_node = add_node(
                    "agent", str(agent["id"]), resource_label("agent", agent)
                )
                add_edge(agent_node, root_id, "uses")

    for workflow_id, workflow in workflows_by_id.items():
        agent_ids = {
            str(node.get("agent_id") or "")
            for node in (workflow.get("definition") or {}).get("nodes", [])
        } - {""}
        if resource_type == "workflow" and workflow_id == canonical_resource_id:
            for agent_id in agent_ids:
                agent = agents.get(agent_id)
                agent_node = add_node(
                    "agent",
                    agent_id,
                    resource_label("agent", agent) if agent else agent_id,
                )
                add_edge(root_id, agent_node, "orchestrates")
        elif resource_type == "agent" and canonical_resource_id in agent_ids:
            workflow_item = resources_by_type["workflow"].get(workflow_id, workflow)
            workflow_node = add_node(
                "workflow", workflow_id, resource_label("workflow", workflow_item)
            )
            add_edge(workflow_node, root_id, "orchestrates")

    return {
        "root_id": root_id,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }


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
    db_val = 1 if verified_val else 0

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT 1 FROM resource_social WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        )
        if not row:
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado en el catálogo social",
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
    new_owner = str(body.get("username") or body.get("owner_id") or "").strip().lower()
    if not new_owner:
        raise APIError(
            400, "invalid_field", "owner_id es obligatorio", extra={"field": "owner_id"}
        )

    async with open_db() as conn:
        user_row = await conn.fetchone(
            "SELECT id, username, is_active FROM users WHERE username=?", (new_owner,)
        )
        if not user_row:
            raise APIError(
                404,
                "not_found",
                "El usuario propietario no existe",
                extra={"resource": "user"},
            )
        if not user_row["is_active"]:
            raise APIError(
                400,
                "invalid_field",
                "El usuario propietario no está activo",
                extra={"field": "owner_id"},
            )
        row = await conn.fetchone(f"SELECT id FROM {table} WHERE id=?", (resource_id,))
        if not row:
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado",
                extra={"resource": resource_type},
            )
        await conn.execute(
            f"UPDATE {table} SET owner_id=? WHERE id=?",
            (user_row["id"], resource_id),
        )
        await conn.commit()
    return {"ok": True}


@admin_router.post("/impersonate/{username}")
async def admin_impersonate(
    username: str,
    response: Response,
    admin: str = Depends(require_admin),
) -> dict[str, Any]:
    target_user = await get_user_by_username(username)
    if not target_user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    if target_user["id"] == admin:
        raise APIError(400, "already_own_user", "Ya eres este usuario")

    # Verificar que la cuenta del usuario objetivo esté activa
    if not target_user.get("is_active", 1):
        raise APIError(
            400,
            "cannot_impersonate_disabled",
            "No se puede impersonar una cuenta desactivada",
        )

    # N3: registrar la impersonación para auditoría de seguridad
    flog.warning(f"[admin] IMPERSONACIÓN: admin={admin!r} → usuario={username!r}")

    # Crear token para el group personal del usuario impersonado
    # (group_id=username por defecto)
    token = create_token(target_user["id"])

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
