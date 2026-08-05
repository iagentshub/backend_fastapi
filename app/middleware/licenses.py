"""License gate for paid product API routes."""

from __future__ import annotations

import json

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.auth import decode_token, get_user_by_identity, get_user_role
from app.config.data import SETTINGS_FILE
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


# Antes esto leía y parseaba settings.json entero en CADA petición —incluidas
# las que el middleware iba a dejar pasar sin mirar—, síncrono y dentro del
# event loop.
#
# La invalidación es explícita, no por mtime: la resolución del mtime no es
# fiable entre dos escrituras seguidas, y un falso acierto de caché aquí
# significa dejar pasar a quien no tiene licencia. En una puerta de cobro eso
# no se negocia. settings.json solo lo escribe _write_platform_cfg dentro del
# proceso (el instalador y el entrypoint escriben antes de arrancar uvicorn),
# así que ese único punto basta para mantener el caché al día.
_cache: bool | None = None


def invalidate_billing_cache() -> None:
    """A llamar tras escribir settings.json. Ver _write_platform_cfg."""
    global _cache
    _cache = None


def _billing_enabled() -> bool:
    global _cache
    if _cache is None:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            _cache = bool(data.get("billing_enabled", False))
        except Exception:
            return False  # sin settings legibles: puerta cerrada, y sin cachearlo
    return _cache


class LicenseGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        # El prefijo primero: descarta la mayoría de peticiones sin tocar disco.
        if not path.startswith(_PROTECTED_PREFIXES) or not _billing_enabled():
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

        user = await get_user_by_identity(username)
        user_id = user["id"] if user else username
        if await BillingStorage().has_active_license(user_id):
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
