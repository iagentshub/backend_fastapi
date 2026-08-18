"""Límite de tamaño para cuerpos HTTP, incluido streaming sin Content-Length.

El número lo decide el administrador desde el panel (`max_request_bytes` en
settings.json) y **por defecto no hay límite**. Antes eran tres límites
distintos para la misma petición —1 MB en nginx por su valor por defecto, 2 MB
aquí y 10 MB anunciados por el cliente—, así que el que mandaba era el de
nginx: quien subía un PDF de 4 MB que la interfaz aceptaba recibía la página
HTML de error de nginx, no el `payload_too_large` con `limit_bytes` que este
middleware fabrica. Ahora nginx no impone techo propio (`client_max_body_size 0`
en `frontend_react/nginx.react.conf`) y el único que rechaza es este, en JSON.

Ver docs/adr/011-un-solo-limite-de-tamano-y-lo-pone-el-admin.md
"""

from __future__ import annotations

import json

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Los módulos enteros, no sus símbolos: SETTINGS_FILE y BODY_MAX_BYTES se
# resuelven al importar y los tests (y el panel de admin, que reescribe
# settings.json en caliente) necesitan ver el valor de ahora.
import app.config.data as _data
import app.config.session as _session
from app.utils import flog

# Sin límite configurado. Es el valor por defecto y hace que el middleware deje
# pasar cualquier cuerpo sin contar un solo byte.
UNLIMITED = 0

# Mismo trato que el caché de billing en licenses.py: se invalida a mano desde
# _write_platform_cfg, no por mtime. Aquí un acierto falso solo aplica un límite
# viejo durante lo que tarde el siguiente guardado, pero leer y parsear
# settings.json en CADA petición —síncrono, dentro del event loop— es
# exactamente lo que aquel caché vino a quitar.
_cache: int | None = None


def invalidate_body_limit_cache() -> None:
    """A llamar tras escribir settings.json. Ver _write_platform_cfg."""
    global _cache
    _cache = None


def configured_max_bytes() -> int:
    """Límite efectivo en bytes; `UNLIMITED` (0) si no hay ninguno."""
    global _cache
    if _cache is None:
        try:
            data = json.loads(_data.SETTINGS_FILE.read_text(encoding="utf-8"))
            raw = data.get("max_request_bytes", _session.BODY_MAX_BYTES)
        except FileNotFoundError:
            # Instalación sin settings.json: manda el entorno. No se cachea,
            # el fichero puede aparecer después.
            return max(_session.BODY_MAX_BYTES, UNLIMITED)
        except (OSError, ValueError, AttributeError) as exc:
            # Un settings.json ilegible no puede dejar la puerta abierta en
            # silencio: se cae al valor del entorno y queda registrado.
            flog.error(f"[body_limit] Settings ilegibles, se usa el entorno: {exc}")
            return max(_session.BODY_MAX_BYTES, UNLIMITED)
        try:
            _cache = max(int(raw), UNLIMITED)
        except (TypeError, ValueError):
            flog.error(
                f"[body_limit] max_request_bytes no es un número ({raw!r}), "
                "se usa el valor del entorno"
            )
            _cache = max(_session.BODY_MAX_BYTES, UNLIMITED)
    return _cache


class _RequestBodyTooLarge(Exception):
    """Señal interna para abortar la lectura antes de procesar el endpoint."""


class BodySizeLimitMiddleware:
    """Rechaza cuerpos mayores que el límite contando los bytes ASGI reales."""

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        if max_bytes is not None and max_bytes < UNLIMITED:
            raise ValueError("max_bytes no puede ser negativo")
        self.app = app
        # None significa «lo que diga el administrador ahora mismo»: un valor
        # fijado en el constructor se quedaría con el de la hora de arranque y
        # el panel no cambiaría nada hasta reiniciar. Los tests sí lo fijan.
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        return configured_max_bytes() if self._max_bytes is None else self._max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        effective_max = self.max_bytes
        if effective_max == UNLIMITED:
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None and content_length > effective_max:
            await self._reject(scope, receive, send, effective_max)
            return

        received_bytes = 0

        async def limited_receive() -> dict:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") != "http.request":
                return message

            received_bytes += len(message.get("body", b""))
            if received_bytes > effective_max:
                raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(scope, receive, send, effective_max)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return max(parsed, 0)
        return None

    async def _reject(
        self, scope: Scope, receive: Receive, send: Send, limit: int
    ) -> None:
        response = JSONResponse(
            {
                "detail": {
                    "code": "payload_too_large",
                    "message": "Payload demasiado grande",
                    "limit_bytes": limit,
                }
            },
            status_code=413,
        )
        await response(scope, receive, send)
