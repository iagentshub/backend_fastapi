"""Pruebas individuales y masivas de conexiones."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.api.routes.auth import GroupContext, require_group, require_group_session
from app.auth.auth import get_user_role
from app.config.session import (
    RATE_IP_FACTOR,
    RATE_TEST_CALLS,
    RATE_TEST_WINDOW,
    RATE_TESTALL_CALLS,
    RATE_TESTALL_WINDOW,
)
from app.connections import get_provider
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter, principal_key
from app.models.request_bodies import ConnectionTestsBody
from app.services.connection_access import connection_access
from app.services.credentials import is_unreadable, test_failure
from app.storage.connection_storage import ConnectionStorage
from app.storage.guest import get_session, is_guest

router = APIRouter(prefix="/api/connections", tags=["connection-diagnostics"])

_storage = ConnectionStorage()
# Ambos endpoints salen a un tercero con la credencial del usuario, así que la
# cuota tiene que ser suya: son superficie de amplificación, no de navegación.
_test_limiter = RateLimiter(
    calls=RATE_TEST_CALLS,
    window=RATE_TEST_WINDOW,
    key_func=principal_key,
    shared=True,
    name="connection-test",
    ip_calls=RATE_TEST_CALLS * RATE_IP_FACTOR,
)
_test_all_limiter = RateLimiter(
    calls=RATE_TESTALL_CALLS,
    window=RATE_TESTALL_WINDOW,
    key_func=principal_key,
    shared=True,
    name="connection-test-all",
    ip_calls=RATE_TESTALL_CALLS * RATE_IP_FACTOR,
)


async def _resolve_connections(
    user: str, group_id: str, include_shared: bool = True
) -> List[Dict[str, Any]]:
    return await connection_access.list_accessible(
        user, group_id, include_shared=include_shared
    )


async def _get_conn_any(
    conn_id: str, user: str, group_id: str
) -> Dict[str, Any] | None:
    return await connection_access.get_accessible(conn_id, user, group_id)


@router.post("/test-all")
async def test_all_connections(
    body: ConnectionTestsBody | None = None,
    ctx: GroupContext = Depends(require_group),
    _rl: None = Depends(_test_all_limiter),
) -> List[Dict[str, Any]]:
    user, group_id = ctx.user, ctx.group_id
    body = body.payload() if body else {}
    ids = body.get("ids") or None
    conns = await _resolve_connections(user, group_id, include_shared=False)
    if ids:
        conns = [c for c in conns if c.get("id") in ids]

    async def _test_one(conn: Dict[str, Any]) -> Dict[str, Any]:
        import time as _time

        if is_unreadable(conn):
            return {"id": conn["id"], "latency_ms": None, **test_failure(conn)}
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


@router.post("/{conn_id}/test")
async def test_connection(
    conn_id: str,
    ctx: GroupContext = Depends(require_group_session),
    _rl: None = Depends(_test_limiter),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        conn = next(
            (c for c in get_session(user).connections if c.get("id") == conn_id), None
        )
    else:
        role = await get_user_role(user)
        if role == "admin":
            conn = await _storage.get(conn_id, None)
        else:
            conn = await _get_conn_any(conn_id, user, group_id)
    if not conn:
        raise APIError(
            404, "not_found", "Conexión no encontrada", extra={"resource": "connection"}
        )
    if is_unreadable(conn):
        return test_failure(conn)
    provider = get_provider(conn.get("type") or "")
    if not provider:
        return {
            "ok": False,
            "message": f"Tipo '{conn.get('type')}' sin proveedor de test",
        }
    result = await asyncio.to_thread(provider.test, conn)
    return {"ok": result.ok, "message": result.message, "detail": result.detail}
