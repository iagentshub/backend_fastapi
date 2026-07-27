"""Rutas de conexiones."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Query, Request

from app.api.routes.auth import WorkspaceContext, require_auth, require_workspace
from app.auth.auth import get_user_role
from app.connections import all_providers, get_provider
from app.config.data import AGENTS_DIR, DB_FILE, SKILLS_DIR
from app.config.security import assert_safe_url
from app.errors import APIError
from app.storage.knowledge import KnowledgeStorage
from app.config.session import (
    RATE_TEST_CALLS,
    RATE_TEST_WINDOW,
    RATE_TESTALL_CALLS,
    RATE_TESTALL_WINDOW,
)
from app.middleware.ratelimit import RateLimiter
from app.storage.db import IS_PG, open_db
from app.storage.guest import get_session, is_guest
from app.storage.storage import AgentStorage, ConnectionStorage, SkillStorage
from app.storage.workspace_shares import WorkspaceShareStorage
from app.storage.workspaces import WorkspaceStorage
from app.utils.origin import compute_origin_type


def _safe_name(name: str, taken: Set[str], hub_label: str) -> str:
    """Devuelve name si está libre, si no añade sufijo (hub_label)."""
    if name not in taken:
        return name
    candidate = f"{name} ({hub_label})"
    if candidate not in taken:
        return candidate
    i = 2
    while f"{candidate} {i}" in taken:
        i += 1
    return f"{candidate} {i}"


router = APIRouter(prefix="/api/connections", tags=["connections"])

_storage = ConnectionStorage(DB_FILE)
_agent_storage = AgentStorage(AGENTS_DIR)
_skill_storage = SkillStorage(SKILLS_DIR)
_know_storage = KnowledgeStorage(DB_FILE)
_shares = WorkspaceShareStorage(DB_FILE)
_ws = WorkspaceStorage(DB_FILE)
_test_limiter = RateLimiter(calls=RATE_TEST_CALLS, window=RATE_TEST_WINDOW)
_test_all_limiter = RateLimiter(calls=RATE_TESTALL_CALLS, window=RATE_TESTALL_WINDOW)
# N2: limitar hub-sync para evitar amplificación de peticiones HTTP externas
# 20 syncs/min por IP es un límite útil en producción sin romper tests
_hub_sync_limiter = RateLimiter(calls=20, window=60)


async def _owner(user: str, workspace_id: str) -> str | None:
    """None → admin ve todo; str → filtra por workspace."""
    return None if await get_user_role(user) == "admin" else workspace_id


async def _list_accessible(user: str, workspace_id: str) -> List[Dict[str, Any]]:
    """Lista conexiones del workspace activo + personales del usuario (en workspace de equipo).

    En workspace personal (workspace_id == user) devuelve solo las propias.
    En workspace de equipo incluye también las personales marcadas con _personal_key=True.
    """
    ws_conns = await _storage.list(workspace_id)
    for c in ws_conns:
        c["owner_id"] = workspace_id
    if workspace_id == user:
        return ws_conns
    personal_conns = await _storage.list(user)
    seen = {c["id"] for c in ws_conns}
    for c in personal_conns:
        if c["id"] not in seen:
            c["owner_id"] = user
            c["_personal_key"] = True
            ws_conns.append(c)
    return ws_conns


async def _get_conn_any(
    conn_id: str, user: str, workspace_id: str
) -> Dict[str, Any] | None:
    """Obtiene una conexión buscando en el workspace activo, el personal, y por último
    entre las compartidas directamente con el workspace (referencia sin duplicar)."""
    conn = await _storage.get(conn_id, workspace_id)
    if conn is None and workspace_id != user:
        conn = await _storage.get(conn_id, user)
    if conn is None:
        granted_ids = await _shares.get_workspace_shared_resource_ids(
            workspace_id, "connection"
        )
        if conn_id in granted_ids:
            async with open_db() as db:
                owner_row = await db.fetchone(
                    "SELECT owner_id FROM connections WHERE id = ?", (conn_id,)
                )
            if owner_row and await _ws.owner_is_active(owner_row[0]):
                conn = await _storage.get(conn_id, None)
                if conn is not None:
                    conn["_shared"] = True
    return conn


async def _resolve_connections(
    user: str, workspace_id: str, include_shared: bool = True
) -> List[Dict[str, Any]]:
    """Devuelve la lista de conexiones visibles para el usuario según su rol."""
    if is_guest(user):
        return list(get_session(user).connections)
    role = await get_user_role(user)
    if role == "admin":
        return await _storage.list(None)
    raw = await _list_accessible(user, workspace_id)
    if include_shared:
        shared_ids = set(
            await _shares.get_workspace_shared_resource_ids(workspace_id, "connection")
        )
        own_ids = {i["id"] for i in raw}
        for rid in shared_ids - own_ids:
            async with open_db() as db:
                owner_row = await db.fetchone(
                    "SELECT owner_id FROM connections WHERE id = ?", (rid,)
                )
            if not owner_row or not await _ws.owner_is_active(owner_row[0]):
                continue
            c = await _storage.get(rid)
            if c:
                c["_shared"] = True
                raw.append(c)
    return raw


def _fetch_ollama_models(host: str) -> List[str]:
    """Llama a /api/tags y devuelve los nombres de modelos instalados."""
    from app.connections.ollama import OllamaProvider
    from app.config.security import assert_safe_url

    try:
        assert_safe_url(host)  # C3: prevenir SSRF via hosts de conexiones almacenadas
        data = OllamaProvider._fetch_tags(host)
    except OSError:
        alt = OllamaProvider._alt_host(host)
        if not alt:
            return []
        try:
            data = OllamaProvider._fetch_tags(alt)
        except Exception:
            return []
    except Exception:
        return []
    return [m["name"] for m in (data.get("models") or []) if m.get("name")]


async def _ollama_conns_to_models(
    ollama_conns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Convierte todas las conexiones Ollama en una lista de entradas por modelo,
    sin duplicados. Las conexiones con modelo específico tienen prioridad sobre
    las generadas por expansión de la conexión base.
    """
    seen: set = set()
    result: List[Dict[str, Any]] = []

    for c in ollama_conns:
        model = (c.get("model") or "").strip()
        if not model:
            continue
        if model in seen:
            continue
        seen.add(model)
        clean = {k: v for k, v in c.items() if k != "api_key"}
        clean["name"] = model
        result.append(clean)

    base_conns = [c for c in ollama_conns if not (c.get("model") or "").strip()]
    if base_conns:
        base = base_conns[0]
        host = (base.get("host") or "http://localhost:11434").rstrip("/")
        models = await asyncio.to_thread(_fetch_ollama_models, host)
        base_clean = {k: v for k, v in base.items() if k != "api_key"}
        if models:
            for model in models:
                if model in seen:
                    continue
                seen.add(model)
                result.append(
                    {
                        **base_clean,
                        "id": f"{base['id']}::{model}",
                        "name": model,
                        "model": model,
                    }
                )
        else:
            result.append(base_clean)

    return result


# IMPORTANTE: las rutas literales (/providers, /raw, /test-all) deben definirse
# ANTES que las rutas con parámetros (/{conn_id}) para que FastAPI las priorice.


@router.get("/raw")
async def list_connections_raw(
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    """Devuelve las conexiones tal como están en BD, sin expansión de modelos Ollama.
    Usado por el perfil para gestionar credenciales base."""
    user, workspace_id = ctx.user, ctx.workspace_id
    raw = await _resolve_connections(user, workspace_id)
    return [{k: v for k, v in c.items() if k != "api_key"} for c in raw]


@router.get("/providers")
async def list_providers(_: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    return all_providers()


@router.post("/ollama-models")
async def ollama_models(
    request: Request,
    _: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Devuelve los modelos instalados en una instancia Ollama."""
    from app.connections.ollama import OllamaProvider

    body = await request.json()
    host = (body.get("host") or "http://localhost:11434").strip().rstrip("/")
    try:
        assert_safe_url(host)
        data = await asyncio.to_thread(OllamaProvider._fetch_tags, host)
        models = [m["name"] for m in (data.get("models") or []) if m.get("name")]
        return {"models": models}
    except Exception as exc:
        return {"models": [], "error": str(exc)}


@router.post("/test-all")
async def test_all_connections(
    request: Request,
    ctx: WorkspaceContext = Depends(require_workspace),
    _rl: None = Depends(_test_all_limiter),
) -> List[Dict[str, Any]]:
    user, workspace_id = ctx.user, ctx.workspace_id
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    ids = body.get("ids") or None
    conns = await _resolve_connections(user, workspace_id, include_shared=False)
    if ids:
        conns = [c for c in conns if c.get("id") in ids]

    async def _test_one(conn: Dict[str, Any]) -> Dict[str, Any]:
        import time as _time

        provider = get_provider(conn.get("type") or "")
        if not provider:
            return {
                "id": conn["id"],
                "ok": False,
                "message": "Sin proveedor de test",
                "detail": "",
                "latency_ms": None,
            }
        t0 = _time.perf_counter()
        result = await asyncio.to_thread(provider.test, conn)
        latency_ms = round((_time.perf_counter() - t0) * 1000)
        return {
            "id": conn["id"],
            "ok": result.ok,
            "message": result.message,
            "detail": result.detail,
            "latency_ms": latency_ms,
        }

    return list(await asyncio.gather(*[_test_one(c) for c in conns]))


@router.get("")
async def list_connections(
    group_id: Optional[str] = None,
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    user, workspace_id = ctx.user, ctx.workspace_id

    if group_id is not None and not is_guest(user):
        role = await get_user_role(user)
        if role != "admin" and not await _ws.can_access(group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        # Devuelve solo las conexiones compartidas con este grupo específico
        shared_ids = set(
            await _shares.get_workspace_shared_resource_ids(group_id, "connection")
        )
        raw: List[Dict[str, Any]] = []
        for rid in shared_ids:
            async with open_db() as db:
                owner_row = await db.fetchone(
                    "SELECT owner_id FROM connections WHERE id = ?", (rid,)
                )
            if not owner_row or not await _ws.owner_is_active(owner_row[0]):
                continue
            c = await _storage.get(rid)
            if c:
                c["_shared"] = True
                c["_group_id"] = group_id
                raw.append(c)
    else:
        raw = await _resolve_connections(user, workspace_id)
        # Para usuarios normales, añadir shares de todos los grupos del usuario
        if not is_guest(user) and await get_user_role(user) != "admin":
            own_ids = {c["id"] for c in raw}
            user_groups = await _ws.list_for_user(user)
            shared_map: Dict[str, str] = {}  # resource_id -> group_id
            for group in user_groups:
                gid = group["id"]
                for rid in await _shares.get_workspace_shared_resource_ids(gid, "connection"):
                    if rid not in shared_map:
                        shared_map[rid] = gid
            for rid in set(shared_map.keys()) - own_ids:
                async with open_db() as db:
                    owner_row = await db.fetchone(
                        "SELECT owner_id FROM connections WHERE id = ?", (rid,)
                    )
                if not owner_row or not await _ws.owner_is_active(owner_row[0]):
                    continue
                c = await _storage.get(rid)
                if c:
                    c["_shared"] = True
                    c["_group_id"] = shared_map[rid]
                    raw.append(c)

    for c in raw:
        if c.get("_shared") or c.get("owner_id") in (user, workspace_id):
            c["origin_type"] = compute_origin_type(c)

    non_ollama = [c for c in raw if c.get("type") != "ollama"]
    ollama_raw = [c for c in raw if c.get("type") == "ollama"]
    if workspace_id != user and not is_guest(user) and await get_user_role(user) != "admin":
        non_ollama = [
            connection for connection in non_ollama
            if await _ws.has_resource_permission(
                workspace_id, user, "connections", connection["id"], "direct"
            )
        ]
        ollama_raw = [
            connection for connection in ollama_raw
            if await _ws.has_resource_permission(
                workspace_id, user, "connections", connection["id"], "direct"
            )
        ]

    result: List[Dict[str, Any]] = [
        {k: v for k, v in c.items() if k != "api_key"} for c in non_ollama
    ]

    if ollama_raw:
        result.extend(await _ollama_conns_to_models(ollama_raw))

    if offset:
        result = result[offset:]
    if limit:
        result = result[:limit]
    return result


@router.post("")
async def save_connection(
    request: Request, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    payload = await request.json()
    scope = payload.pop("scope", "workspace")
    if not get_provider(payload.get("type") or ""):
        raise APIError(
            422,
            "invalid_field",
            "Tipo de conexión no válido",
            extra={"field": "connection_type"},
        )

    # Las conexiones son siempre privadas — se pueden compartir con un workspace completo
    labels = [lbl for lbl in (payload.get("labels") or []) if lbl != "public"]
    if "private" not in labels:
        labels = ["private"] + labels
    payload["labels"] = labels

    if is_guest(user):
        s = get_session(user)
        conn: Dict[str, Any] = {**payload, "id": payload.get("id") or uuid4().hex[:12]}
        s.connections = [c for c in s.connections if c.get("id") != conn["id"]]
        s.connections.append(conn)
        return {k: v for k, v in conn.items() if k != "api_key"}
    owner = user if scope == "personal" else workspace_id
    conn = await _storage.save(payload, owner_id=owner)
    return {k: v for k, v in conn.items() if k != "api_key"}


@router.get("/tokens-daily")
async def get_tokens_daily(
    days: int = 14,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    import datetime as _dt

    days = max(1, min(days, 90))
    cutoff = (_dt.date.today() - _dt.timedelta(days=days - 1)).isoformat()
    today = _dt.date.today().isoformat()
    workspace_id = ctx.workspace_id

    async with open_db() as conn:
        try:
            rows = await conn.fetchall(
                "SELECT day, SUM(tokens) FROM token_daily "
                "WHERE owner_id = ? AND day >= ? GROUP BY day ORDER BY day ASC",
                (workspace_id, cutoff),
            )
            if not rows:
                if IS_PG:
                    await conn.execute(
                        "INSERT INTO token_daily (day, owner_id, tokens) "
                        "SELECT ?, owner_id, tokens_in + tokens_out FROM connections "
                        "WHERE owner_id = ? AND tokens_in + tokens_out > 0 "
                        "ON CONFLICT (day, owner_id) DO NOTHING",
                        (today, workspace_id),
                    )
                else:
                    await conn.execute(
                        "INSERT OR IGNORE INTO token_daily (day, owner_id, tokens) "
                        "SELECT ?, owner_id, tokens_in + tokens_out FROM connections "
                        "WHERE owner_id = ? AND tokens_in + tokens_out > 0",
                        (today, workspace_id),
                    )
                await conn.commit()
                rows = await conn.fetchall(
                    "SELECT day, SUM(tokens) FROM token_daily "
                    "WHERE owner_id = ? AND day >= ? GROUP BY day ORDER BY day ASC",
                    (workspace_id, cutoff),
                )
        except Exception:
            rows = []

    return [{"day": r[0], "tokens": r[1]} for r in rows]


@router.get("/{conn_id}")
async def get_connection(
    conn_id: str, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        conn = next(
            (c for c in get_session(user).connections if c.get("id") == conn_id), None
        )
    else:
        role = await get_user_role(user)
        if role == "admin":
            conn = await _storage.get(conn_id, None)
        else:
            conn = await _get_conn_any(conn_id, user, workspace_id)
    if not conn:
        raise APIError(404, "not_found", "Conexión no encontrada", extra={"resource": "connection"})
    if (
        workspace_id != user
        and not is_guest(user)
        and await get_user_role(user) != "admin"
        and not await _ws.has_resource_permission(
            workspace_id, user, "connections", conn_id, "direct"
        )
    ):
        raise APIError(403, "forbidden", "Sin permiso para usar esta conexión")
    conn["origin_type"] = compute_origin_type(conn)
    return {k: v for k, v in conn.items() if k != "api_key"}


@router.delete("/{conn_id}")
async def delete_connection(
    conn_id: str, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        s = get_session(user)
        before = len(s.connections)
        s.connections = [c for c in s.connections if c.get("id") != conn_id]
        if len(s.connections) == before:
            raise APIError(404, "not_found", "Conexión no encontrada", extra={"resource": "connection"})
        return {"ok": True}
    owner_id = await _owner(user, workspace_id)
    deleted = await _storage.delete(conn_id, owner_id)
    if not deleted and workspace_id != user:
        deleted = await _storage.delete(conn_id, user)
    if not deleted:
        raise APIError(404, "not_found", "Conexión no encontrada", extra={"resource": "connection"})
    return {"ok": True}


@router.post("/{conn_id}/hub-sync")
async def hub_sync(
    conn_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
    _rl: None = Depends(_hub_sync_limiter),  # N2: prevenir amplificación HTTP
) -> Dict[str, Any]:
    """Sincroniza agentes, skills, conocimiento y conexiones desde un hub remoto."""
    user, workspace_id = ctx.user, ctx.workspace_id
    role = await get_user_role(user)
    if role == "admin":
        conn = await _storage.get(conn_id, None)
    else:
        conn = await _get_conn_any(conn_id, user, workspace_id)
    if not conn:
        raise APIError(404, "not_found", "Conexión no encontrada", extra={"resource": "connection"})
    if conn.get("type") != "iagentshub":
        raise APIError(
            400,
            "hub_sync_invalid_type",
            "Solo disponible para conexiones de tipo iAgents Hub",
        )

    url = (conn.get("url") or "").rstrip("/")
    username = conn.get("username") or ""
    password = conn.get("api_key") or ""
    hub_label = conn.get("name") or "Hub"
    owner = workspace_id

    # C1: validar URL contra SSRF antes de hacer cualquier petición HTTP
    from app.config.security import assert_safe_url as _assert_safe_hub_url
    try:
        _assert_safe_hub_url(url)
    except ValueError as _ssrf_err:
        raise APIError(400, "unsafe_url", str(_ssrf_err))

    from app.connections.iagentshub import _login

    try:
        token = _login(url, username, password)
    except Exception as e:
        raise APIError(
            502, "hub_auth_error", f"Error de autenticación: {e}", extra={"reason": str(e)}
        )

    headers = {"Cookie": f"ga_token={token}"}
    result: Dict[str, Any] = {
        "agents": 0,
        "skills": 0,
        "knowledge": 0,
        "connections": 0,
        "updated": 0,
        "errors": [],
    }

    async def _get(path: str) -> Any:
        r = await client.get(f"{url}{path}", headers=headers)
        r.raise_for_status()
        return r.json()

    async with httpx.AsyncClient(timeout=30) as client:
        # ── 1. Conexiones (solo estructura, sin credenciales) ──────────────
        try:
            remote_conns = await _get("/api/connections")

            local_conns = await _storage.list(owner)
            local_conn_names: Set[str] = {c["name"] for c in local_conns}
            by_src = {
                c.get("_hub_source"): c for c in local_conns if c.get("_hub_source")
            }
            conns_created = 0
            conns_updated = 0
            for rc in remote_conns:
                src_key = f"{conn_id}:{rc.get('id', '')}"
                data: Dict[str, Any] = {
                    "type": rc.get("type", ""),
                    "model": rc.get("model") or "",
                    "url": rc.get("url") or "",
                    "host": rc.get("host") or "",
                    "_imported": True,
                    "_hub_source": src_key,
                }
                if src_key in by_src:
                    data["id"] = by_src[src_key]["id"]
                    data["name"] = by_src[src_key]["name"]
                    await _storage.save(data, owner_id=owner)
                    conns_updated += 1
                else:
                    name = _safe_name(
                        rc.get("name", "Conexión"), local_conn_names, hub_label
                    )
                    data["name"] = name
                    local_conn_names.add(name)
                    await _storage.save(data, owner_id=owner)
                    conns_created += 1
            result["connections"] += conns_created
            result["updated"] += conns_updated
        except Exception as e:
            result["errors"].append(f"conexiones: {e}")

        # ── 2. Agentes ────────────────────────────────────────────────────
        try:
            summaries = await _get("/api/agents?scope=private")
            local_agents = await _agent_storage.list("private")
            local_a_names: Set[str] = {a["name"] for a in local_agents}
            by_src = {
                a.get("_hub_source"): a for a in local_agents if a.get("_hub_source")
            }

            for summary in summaries:
                ra_id = summary.get("id", "")
                src_key = f"{conn_id}:{ra_id}"
                try:
                    ra = await _get(f"/api/agents/{ra_id}")
                except Exception:
                    continue

                data = {
                    k: v
                    for k, v in ra.items()
                    if k
                    not in (
                        "id",
                        "owner_id",
                        "created_at",
                        "updated_at",
                        "tokens_in",
                        "tokens_out",
                    )
                }
                data["_hub_source"] = src_key
                data["_hub_conn_id"] = conn_id

                if src_key in by_src:
                    data["id"] = by_src[src_key]["id"]
                    await _agent_storage.save(data, "private", owner_id=owner)
                    result["updated"] += 1
                else:
                    name = _safe_name(
                        ra.get("name", "Agente"), local_a_names, hub_label
                    )
                    data["name"] = name
                    local_a_names.add(name)
                    await _agent_storage.save(data, "private", owner_id=owner)
                    result["agents"] += 1
        except Exception as e:
            result["errors"].append(f"agentes: {e}")

        # ── 3. Skills ────────────────────────────────────────────────────
        try:
            remote_skills = await _get("/api/skills?scope=private")
            local_skills = await _skill_storage.list("private")
            local_s_names: Set[str] = {s["name"] for s in local_skills}
            by_src = {
                s.get("_hub_source"): s for s in local_skills if s.get("_hub_source")
            }

            for rs in remote_skills:
                rs_id = rs.get("id", "")
                src_key = f"{conn_id}:{rs_id}"
                data = {
                    k: v
                    for k, v in rs.items()
                    if k not in ("id", "owner_id", "created_at", "updated_at")
                }
                data["_hub_source"] = src_key
                data["_hub_conn_id"] = conn_id

                if src_key in by_src:
                    data["id"] = by_src[src_key]["id"]
                    await _skill_storage.save("private", data, owner_id=owner)
                    result["updated"] += 1
                else:
                    name = _safe_name(rs.get("name", "Skill"), local_s_names, hub_label)
                    data["name"] = name
                    local_s_names.add(name)
                    await _skill_storage.save("private", data, owner_id=owner)
                    result["skills"] += 1
        except Exception as e:
            result["errors"].append(f"skills: {e}")

        # ── 4. Conocimiento ───────────────────────────────────────────────
        try:
            remote_know = await _get("/api/knowledge")

            local_know = await _know_storage.list(owner)
            local_k_titles: Set[str] = {k["title"] for k in local_know}
            synced_srcs = {
                k.get("source", "")
                for k in local_know
                if k.get("source", "").startswith(f"hub:{conn_id}:")
            }
            know_created = 0
            know_updated = 0
            for rk in remote_know:
                rk_id = rk.get("id", "")
                src_tag = f"hub:{conn_id}:{rk_id}"
                if src_tag in synced_srcs:
                    know_updated += 1
                    continue
                title = _safe_name(rk.get("title", ""), local_k_titles, hub_label)
                local_k_titles.add(title)
                try:
                    await _know_storage.save(
                        type=rk.get("type", "url"),
                        title=title,
                        source=src_tag,
                        content=rk.get("content", ""),
                        owner_id=owner,
                    )
                    know_created += 1
                except Exception:
                    pass
            result["knowledge"] += know_created
            result["updated"] += know_updated
        except Exception as e:
            result["errors"].append(f"conocimiento: {e}")

    result["ok"] = not result["errors"]
    return result


@router.post("/{conn_id}/import-models")
async def import_models(
    conn_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    """Descubre modelos de la conexión-credencial y crea una conexión por modelo."""
    user, workspace_id = ctx.user, ctx.workspace_id
    role = await get_user_role(user)
    if role == "admin":
        conn = await _storage.get(conn_id, None)
    else:
        conn = await _get_conn_any(conn_id, user, workspace_id)
    if not conn:
        raise APIError(404, "not_found", "Conexión no encontrada", extra={"resource": "connection"})

    conn_type = conn.get("type", "")

    from app.api.routes.accounts import _fetch_models

    _PROVIDER_TYPE_TO_ACCOUNT: Dict[str, str] = {
        "claude": "anthropic",
        "gemini": "google",
        "openai": "openai",
        "ollama": "ollama",
        "nvidia": "nvidia",
        "qwen": "qwen",
        "grok": "grok",
    }
    account_key = _PROVIDER_TYPE_TO_ACCOUNT.get(conn_type)
    if not account_key:
        raise APIError(
            400,
            "import_models_unsupported",
            f"Import de modelos no soportado para '{conn_type}'",
            extra={"type": conn_type},
        )

    api_key = conn.get("api_key", "")
    host = conn.get("host", "") or conn.get("url", "")
    models = await _fetch_models(account_key, api_key, host)
    if not models:
        raise APIError(
            502, "no_models_found", "No se encontraron modelos en este proveedor"
        )

    owner_id = await _owner(user, workspace_id)
    owner = conn.get("owner_id") or (owner_id or workspace_id)

    existing = await _storage.list(owner)
    existing_by_model = {
        c["model"]: c
        for c in existing
        if c.get("type") == conn_type and c.get("_source_conn") == conn_id
    }
    created = 0
    updated = 0
    for model_id in models:
        data: Dict[str, Any] = {
            "name": f"{conn.get('name', conn_type)} / {model_id}",
            "type": conn_type,
            "api_key": api_key,
            "model": model_id,
            "_imported": True,
            "_source_conn": conn_id,
        }
        if conn.get("host"):
            data["host"] = conn["host"]
        if conn.get("url"):
            data["url"] = conn["url"]
        if model_id in existing_by_model:
            data["id"] = existing_by_model[model_id]["id"]
            updated += 1
        else:
            created += 1
        await _storage.save(data, owner_id=owner)
    return {"ok": True, "created": created, "updated": updated, "models": models}


@router.post("/{conn_id}/test")
async def test_connection(
    conn_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
    _rl: None = Depends(_test_limiter),
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        conn = next(
            (c for c in get_session(user).connections if c.get("id") == conn_id), None
        )
    else:
        role = await get_user_role(user)
        if role == "admin":
            conn = await _storage.get(conn_id, None)
        else:
            conn = await _get_conn_any(conn_id, user, workspace_id)
    if not conn:
        raise APIError(404, "not_found", "Conexión no encontrada", extra={"resource": "connection"})
    provider = get_provider(conn.get("type") or "")
    if not provider:
        return {
            "ok": False,
            "message": f"Tipo '{conn.get('type')}' sin proveedor de test",
        }
    result = await asyncio.to_thread(provider.test, conn)
    return {"ok": result.ok, "message": result.message, "detail": result.detail}
