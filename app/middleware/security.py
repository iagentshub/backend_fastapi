"""Middleware que añade cabeceras de seguridad HTTP a todas las respuestas."""
import os

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# HSTS solo en producción HTTPS para no romper el flujo HTTP de desarrollo local.
_frontend_url = os.getenv("GAIA_FRONTEND_URL", "")
_HTTPS_ENABLED = _frontend_url.startswith("https://") or os.getenv("GAIA_SECURE_COOKIES", "").lower() == "true"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'",
        )
        # N1: HSTS — solo en producción HTTPS para prevenir protocol downgrade
        if _HTTPS_ENABLED:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
