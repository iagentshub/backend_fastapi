"""Middleware ASGI que añade cabeceras de seguridad a las respuestas HTTP."""
import os

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# HSTS solo en producción HTTPS para no romper el flujo HTTP de desarrollo local.
_frontend_url = os.getenv("GAIA_FRONTEND_URL", "")
_HTTPS_ENABLED = _frontend_url.startswith("https://") or os.getenv("GAIA_SECURE_COOKIES", "").lower() == "true"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                self._setdefault(headers, "X-Content-Type-Options", "nosniff")
                self._setdefault(headers, "X-Frame-Options", "DENY")
                self._setdefault(
                    headers, "Referrer-Policy", "strict-origin-when-cross-origin"
                )
                self._setdefault(
                    headers,
                    "Permissions-Policy",
                    "camera=(), microphone=(), geolocation=()",
                )
                self._setdefault(
                    headers,
                    "Content-Security-Policy",
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob:; "
                    "connect-src 'self'; "
                    "font-src 'self'; "
                    "frame-ancestors 'none'",
                )
                if _HTTPS_ENABLED:
                    self._setdefault(
                        headers,
                        "Strict-Transport-Security",
                        "max-age=31536000; includeSubDomains",
                    )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)

    @staticmethod
    def _setdefault(headers: MutableHeaders, name: str, value: str) -> None:
        if name not in headers:
            headers[name] = value
