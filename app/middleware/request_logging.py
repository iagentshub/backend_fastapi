"""Logging HTTP estructurado con duración, IP confiable y usuario de sesión."""

from __future__ import annotations

import time

from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.utils import flog
from app.utils.net import client_ip


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started_at = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started_at) * 1000

        username = self._username_for_log(request)
        message = (
            f"{request.method} {request.url.path} → {response.status_code} "
            f"({elapsed_ms:.0f}ms)"
        )
        context = {"ip": client_ip(request) or "-", "username": username}
        if response.status_code >= 500:
            flog.error(message, **context)
        elif response.status_code >= 400:
            flog.warning(message, **context)
        else:
            flog.info(message, **context)
        return response

    @staticmethod
    def _username_for_log(request: Request) -> str:
        token = request.cookies.get("ga_token", "")
        if token:
            try:
                payload = jwt.get_unverified_claims(token)
                value = payload.get("sub") or payload.get("username")
                if isinstance(value, str) and value:
                    return value
            except Exception:
                return "-"
        if request.cookies.get("ga_guest"):
            return "guest"
        return "-"
