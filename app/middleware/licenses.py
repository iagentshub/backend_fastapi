"""License gate for paid product API routes."""

from __future__ import annotations

import json

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.auth import decode_token, get_user_role
from app.config.data import DB_FILE, SETTINGS_FILE
from app.storage.billing import BillingStorage
from app.storage.guest import is_guest

_PROTECTED_PREFIXES = (
    "/api/agents",
    "/api/connections",
    "/api/knowledge",
    "/api/memory",
    "/api/chats",
    "/api/groups",
)


def _billing_enabled() -> bool:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("billing_enabled", False))


class LicenseGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if not _billing_enabled() or not path.startswith(_PROTECTED_PREFIXES):
            return await call_next(request)

        token = request.cookies.get("ga_token")
        username = decode_token(token) if token else None
        if not username:
            return await call_next(request)
        if is_guest(username):
            return await call_next(request)

        role = await get_user_role(username)
        if role == "admin":
            return await call_next(request)

        if await BillingStorage(DB_FILE).has_active_license(username):
            return await call_next(request)

        return JSONResponse(
            {
                "detail": {
                    "code": "license_required",
                    "message": "Se requiere una licencia activa para usar esta función",
                }
            },
            status_code=403,
        )
