"""Rutas de conexiones."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Set
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_role
from app.connections import all_providers, get_provider
from app.config.data import AGENTS_DIR, DB_FILE, SKILLS_DIR
from app.storage.knowledge import KnowledgeStorage
from app.storage.teams import TeamStorage as _TeamStorage
from app.config.session import RATE_TEST_CALLS, RATE_TEST_WINDOW
from app.middleware.ratelimit import RateLimiter
from app.storage.guest import get_session, is_guest
from app.storage.storage import AgentStorage, ConnectionStorage, SkillStorage


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

_storage        = ConnectionStorage(DB_FILE)
_agent_storage  = AgentStorage(AGENTS_DIR)
_skill_storage  = SkillStorage(SKILLS_DIR)
_know_storage   = KnowledgeStorage(DB_FILE)
_test_limiter   = RateLimiter(calls=RATE_TEST_CALLS, window=RATE_TEST_WINDOW)
_sharing_ts     = _TeamStorage(DB_FILE)


def _owner(user: str) -> str | None:
    """None → admin ve todo; str → filtra por owner."""
    return None if get_user_role(user) == "admin" else user


# IMPORTANTE: las rutas literales (/providers, /test-all) deben definirse
# ANTES que las rutas con parámetros (/{conn_id}) para que FastAPI las priorice.

@router.get("/providers")
async def list_providers(_: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    return all_providers()


@router.post("/test-all")
async def test_all_connections(
    request: Request,
    user: str = Depends(require_auth),
    _rl: None = Depends(_test_limiter),
) -> List[Dict[str, Any]]:
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    ids = body.get("ids") or None
    conns = get_session(user).connections if is_guest(user) else _storage.list(_owner(user))
    if ids:
        conns = [c for c in conns if c.get("id") in ids]

    async def _test_one(conn: Dict[str, Any]) -> Dict[str, Any]:
        provider = get_provider(conn.get("type") or "")
        if not provider:
            return {"id": conn["id"], "ok": False, "message": "Sin proveedor de test", "detail": ""}
        result = await asyncio.to_thread(provider.test, conn)
        return {"id": conn["id"], "ok": result.ok, "message": result.message, "detail": result.detail}

    return list(await asyncio.gather(*[_test_one(c) for c in conns]))


@router.get("")
async def list_connections(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    if is_guest(user):
        return [{k: v for k, v in c.items() if k != "api_key"} for c in get_session(user).connections]
    role = get_user_role(user)
    items = _storage.list(_owner(user))
    if role not in ("admin",):
        shared_ids = set(_sharing_ts.get_user_shared_resource_ids(user, "connection"))
        own_ids = {i["id"] for i in items}
        for rid in (shared_ids - own_ids):
            c = _storage.get(rid)
            if c:
                c["_shared"] = True
                items.append(c)
    return [{k: v for k, v in c.items() if k not in ("api_key",)} for c in items]


@router.post("")
async def save_connection(
    request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    payload = await request.json()
    if not get_provider(payload.get("type") or ""):
        raise HTTPException(status_code=422, detail="Tipo de conexión no válido")
    if is_guest(user):
        s = get_session(user)
        conn: Dict[str, Any] = {**payload, "id": payload.get("id") or uuid4().hex[:12]}
        s.connections = [c for c in s.connections if c.get("id") != conn["id"]]
        s.connections.append(conn)
        return {k: v for k, v in conn.items() if k != "api_key"}
    conn = _storage.save(payload, owner_id=user)
    return {k: v for k, v in conn.items() if k != "api_key"}


@router.get("/{conn_id}")
async def get_connection(
    conn_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        conn = next((c for c in get_session(user).connections if c.get("id") == conn_id), None)
    else:
        conn = _storage.get(conn_id, _owner(user))
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    return conn


@router.delete("/{conn_id}")
async def delete_connection(
    conn_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        s = get_session(user)
        before = len(s.connections)
        s.connections = [c for c in s.connections if c.get("id") != conn_id]
        if len(s.connections) == before:
            raise HTTPException(status_code=404, detail="Conexión no encontrada")
        return {"ok": True}
    if not _storage.delete(conn_id, _owner(user)):
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    return {"ok": True}


@router.post("/{conn_id}/hub-sync")
async def hub_sync(
    conn_id: str,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Sincroniza agentes, skills, conocimiento y conexiones desde un hub remoto."""
    conn = _storage.get(conn_id, _owner(user))
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    if conn.get("type") != "iagentshub":
        raise HTTPException(status_code=400, detail="Solo disponible para conexiones de tipo iAgents Hub")

    url       = (conn.get("url") or "").rstrip("/")
    username  = conn.get("username") or ""
    password  = conn.get("api_key") or ""
    hub_label = conn.get("name") or "Hub"
    owner     = user  # siempre el usuario real para writes

    from app.connections.iagentshub import _login
    try:
        token = _login(url, username, password)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error de autenticación: {e}")

    headers = {"Cookie": f"ga_token={token}"}
    result: Dict[str, Any] = {"agents": 0, "skills": 0, "knowledge": 0, "connections": 0, "updated": 0, "errors": []}

    async def _get(path: str) -> Any:
        r = await client.get(f"{url}{path}", headers=headers)
        r.raise_for_status()
        return r.json()

    async with httpx.AsyncClient(timeout=30) as client:

        # ── 1. Conexiones (solo estructura, sin credenciales) ──────────────
        try:
            remote_conns = await _get("/api/connections")
            local_conns  = _storage.list(owner)
            local_conn_names: Set[str] = {c["name"] for c in local_conns}
            by_src = {c.get("_hub_source"): c for c in local_conns if c.get("_hub_source")}

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
                    data["id"]   = by_src[src_key]["id"]
                    data["name"] = by_src[src_key]["name"]
                    _storage.save(data, owner_id=owner)
                    result["updated"] += 1
                else:
                    name = _safe_name(rc.get("name", "Conexión"), local_conn_names, hub_label)
                    data["name"] = name
                    local_conn_names.add(name)
                    _storage.save(data, owner_id=owner)
                    result["connections"] += 1
        except Exception as e:
            result["errors"].append(f"conexiones: {e}")

        # ── 2. Agentes ────────────────────────────────────────────────────
        try:
            summaries     = await _get("/api/agents?scope=private")
            local_agents  = _agent_storage.list("private")
            local_a_names: Set[str] = {a["name"] for a in local_agents}
            by_src = {a.get("_hub_source"): a for a in local_agents if a.get("_hub_source")}

            for summary in summaries:
                ra_id   = summary.get("id", "")
                src_key = f"{conn_id}:{ra_id}"
                try:
                    ra = await _get(f"/api/agents/{ra_id}")
                except Exception:
                    continue

                data = {k: v for k, v in ra.items()
                        if k not in ("id", "owner_id", "created_at", "updated_at", "tokens_in", "tokens_out")}
                data["_hub_source"]  = src_key
                data["_hub_conn_id"] = conn_id

                if src_key in by_src:
                    data["id"] = by_src[src_key]["id"]
                    _agent_storage.save(data, "private", owner_id=owner)
                    result["updated"] += 1
                else:
                    name = _safe_name(ra.get("name", "Agente"), local_a_names, hub_label)
                    data["name"] = name
                    local_a_names.add(name)
                    _agent_storage.save(data, "private", owner_id=owner)
                    result["agents"] += 1
        except Exception as e:
            result["errors"].append(f"agentes: {e}")

        # ── 3. Skills ────────────────────────────────────────────────────
        try:
            remote_skills  = await _get("/api/skills?scope=private")
            local_skills   = _skill_storage.list("private")
            local_s_names: Set[str] = {s["name"] for s in local_skills}
            by_src = {s.get("_hub_source"): s for s in local_skills if s.get("_hub_source")}

            for rs in remote_skills:
                rs_id   = rs.get("id", "")
                src_key = f"{conn_id}:{rs_id}"
                data = {k: v for k, v in rs.items()
                        if k not in ("id", "owner_id", "created_at", "updated_at")}
                data["_hub_source"]  = src_key
                data["_hub_conn_id"] = conn_id

                if src_key in by_src:
                    data["id"] = by_src[src_key]["id"]
                    _skill_storage.save("private", data, owner_id=owner)
                    result["updated"] += 1
                else:
                    name = _safe_name(rs.get("name", "Skill"), local_s_names, hub_label)
                    data["name"] = name
                    local_s_names.add(name)
                    _skill_storage.save("private", data, owner_id=owner)
                    result["skills"] += 1
        except Exception as e:
            result["errors"].append(f"skills: {e}")

        # ── 4. Conocimiento ───────────────────────────────────────────────
        try:
            remote_know   = await _get("/api/knowledge")
            local_know    = _know_storage.list(owner)
            local_k_titles: Set[str] = {k["title"] for k in local_know}
            synced_srcs   = {k.get("source", "") for k in local_know if k.get("source", "").startswith(f"hub:{conn_id}:")}

            for rk in remote_know:
                rk_id   = rk.get("id", "")
                src_tag = f"hub:{conn_id}:{rk_id}"
                if src_tag in synced_srcs:
                    result["updated"] += 1
                    continue
                title = _safe_name(rk.get("title", ""), local_k_titles, hub_label)
                local_k_titles.add(title)
                try:
                    _know_storage.save(
                        type=rk.get("type", "url"),
                        title=title,
                        source=src_tag,
                        content=rk.get("content", ""),
                        owner_id=owner,
                    )
                    result["knowledge"] += 1
                except Exception:
                    pass
        except Exception as e:
            result["errors"].append(f"conocimiento: {e}")

    result["ok"] = not result["errors"]
    return result


@router.post("/{conn_id}/import-models")
async def import_models(
    conn_id: str,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Descubre modelos de la conexión-credencial y crea una conexión por modelo."""
    conn = _storage.get(conn_id, _owner(user))
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada")

    conn_type = conn.get("type", "")

    # Reutilizamos la lógica de fetch_models del sistema de accounts
    from app.api.routes.accounts import _fetch_models
    _PROVIDER_TYPE_TO_ACCOUNT: Dict[str, str] = {
        "claude": "anthropic", "gemini": "google", "openai": "openai",
        "ollama": "ollama", "nvidia": "nvidia", "qwen": "qwen", "grok": "grok",
    }
    account_key = _PROVIDER_TYPE_TO_ACCOUNT.get(conn_type)
    if not account_key:
        raise HTTPException(status_code=400, detail=f"Import de modelos no soportado para '{conn_type}'")

    api_key = conn.get("api_key", "")
    host = conn.get("host", "") or conn.get("url", "")
    models = await _fetch_models(account_key, api_key, host)
    if not models:
        raise HTTPException(status_code=502, detail="No se encontraron modelos en este proveedor")

    owner = _owner(user)
    existing = _storage.list(owner)
    # Existing imported connections for this credential
    existing_by_model = {
        c["model"]: c for c in existing
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
        _storage.save(data, owner_id=owner)

    return {"ok": True, "created": created, "updated": updated, "models": models}


@router.post("/{conn_id}/test")
async def test_connection(
    conn_id: str,
    user: str = Depends(require_auth),
    _rl: None = Depends(_test_limiter),
) -> Dict[str, Any]:
    if is_guest(user):
        conn = next((c for c in get_session(user).connections if c.get("id") == conn_id), None)
    else:
        conn = _storage.get(conn_id, _owner(user))
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    provider = get_provider(conn.get("type") or "")
    if not provider:
        return {"ok": False, "message": f"Tipo '{conn.get('type')}' sin proveedor de test"}
    result = await asyncio.to_thread(provider.test, conn)
    return {"ok": result.ok, "message": result.message, "detail": result.detail}
