"""License gate for paid product API routes."""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.auth import decode_token, get_user_by_identity, get_user_role
from app.storage.billing import BillingStorage
from app.storage.guest import is_guest
from app.utils import flog

_PROTECTED_PREFIXES = (
    "/api/agents",
    "/api/connections",
    "/api/knowledge",
    "/api/memory",
    "/api/chats",
    "/api/groups",
)


# El caché del contenido de settings.json vive en app.services.platform_settings
# y lo comparten los tres lectores. Aquí había uno propio, una variable global
# invalidada solo desde la escritura: con `GAIA_WORKERS` procesos (4 por
# defecto) el guardado del admin lo atiende uno, y los otros tres seguían
# sirviendo el valor viejo hasta el reinicio. En una puerta de cobro eso es
# «a algunos les sigue funcionando gratis», sin nada que lo delate.
#
# La objeción de aquel comentario —el mtime en segundos no distingue dos
# escrituras seguidas, y un falso acierto aquí deja pasar a quien no ha
# pagado— sigue siendo válida y está atendida donde ahora vive el caché:
# `st_mtime_ns`, más invalidación explícita en la escritura, más un TTL corto.


def invalidate_billing_cache() -> None:
    """Compatibilidad: el caché es ahora el de platform_settings."""
    from app.services.platform_settings import invalidate_platform_cfg_cache

    invalidate_platform_cfg_cache()


def _billing_enabled() -> bool:
    from app.services.platform_settings import load_settings_raw

    try:
        return bool(load_settings_raw().get("billing_enabled", False))
    except (OSError, ValueError, AttributeError) as exc:
        # Puerta cerrada. Aquí se registra: esto es una puerta de cobro, y "a
        # todo el mundo le funciona gratis" tiene que poder rastrearse hasta un
        # settings.json ilegible.
        flog.error(f"[licenses] Settings ilegibles, billing desactivado: {exc}")
        return False


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
