"""Rutas de conexiones."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_role
from app.connections import all_providers, get_provider
from app.config.data import DB_FILE
from app.config.session import RATE_TEST_CALLS, RATE_TEST_WINDOW
from app.middleware.ratelimit import RateLimiter
from app.storage.guest import get_session, is_guest
from app.storage.storage import ConnectionStorage

router = APIRouter(prefix="/api/connections", tags=["connections"])

_storage = ConnectionStorage(DB_FILE)
_test_limiter = RateLimiter(calls=RATE_TEST_CALLS, window=RATE_TEST_WINDOW)


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
    items = _storage.list(_owner(user))
    return [{k: v for k, v in c.items() if k != "api_key"} for c in items]


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
