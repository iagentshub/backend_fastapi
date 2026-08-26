"""Puerta de suscripción del servicio gestionado.

No tiene relación con la licencia del software (AGPL-3.0): solo se
activa cuando el administrador enciende `billing_enabled`, que es lo
que distingue al cloud gestionado de una instalación self-hosted.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

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


class LicenseGateMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path
        # El prefijo primero: descarta la mayoría de peticiones sin tocar disco.
        if not path.startswith(_PROTECTED_PREFIXES) or not _billing_enabled():
            await self.app(scope, receive, send)
            return

        token = request.cookies.get("ga_token")
        username = decode_token(token) if token else None
        if not username:
            await self.app(scope, receive, send)
            return
        if is_guest(username):
            await self.app(scope, receive, send)
            return

        role = await get_user_role(username)
        if role == "admin":
            await self.app(scope, receive, send)
            return

        user = await get_user_by_identity(username)
        user_id = user["id"] if user else username
        if await BillingStorage().has_active_license(user_id):
            await self.app(scope, receive, send)
            return

        # El producto se distribuye bajo AGPL-3.0, así que «licencia» pasó a
        # significar la licencia del software —que no restringe ninguna
        # función— y no la plaza de suscripción que se comprueba aquí. Un 403
        # que dijera «se requiere una licencia activa» se lee, desde que el
        # repositorio es AGPL, como que el software no es libre. Lo que falta
        # es la suscripción al servicio gestionado, y eso es lo que se nombra.
        response = JSONResponse(
            {
                "detail": {
                    "code": "subscription_required",
                    "message": (
                        "Se requiere una suscripción activa del servicio "
                        "gestionado para usar esta función"
                    ),
                }
            },
            status_code=403,
        )
        await response(scope, receive, send)
