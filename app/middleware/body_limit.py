"""Límite de tamaño para cuerpos HTTP, incluido streaming sin Content-Length."""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config.session import BODY_MAX_BYTES


class _RequestBodyTooLarge(Exception):
    """Señal interna para abortar la lectura antes de procesar el endpoint."""


class BodySizeLimitMiddleware:
    """Rechaza cuerpos mayores que el límite contando los bytes ASGI reales."""

    def __init__(self, app: ASGIApp, max_bytes: int = BODY_MAX_BYTES) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes debe ser mayor que cero")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None and content_length > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received_bytes = 0

        async def limited_receive() -> dict:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") != "http.request":
                return message

            received_bytes += len(message.get("body", b""))
            if received_bytes > self.max_bytes:
                raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(scope, receive, send)

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

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {
                "detail": {
                    "code": "payload_too_large",
                    "message": "Payload demasiado grande",
                    "limit_bytes": self.max_bytes,
                }
            },
            status_code=413,
        )
        await response(scope, receive, send)
