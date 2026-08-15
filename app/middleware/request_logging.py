"""Logging HTTP estructurado con duración, IP confiable y usuario de sesión."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.auth.passwords import decode_token
from app.config import logging as _log_cfg
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

        if self._silenciar(request, response):
            return response

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
    def _silenciar(request: Request, response: Response) -> bool:
        """Sondas de vida correctas: ruido puro. Si fallan, se registran.

        Se lee del módulo en cada llamada, no por valor, para que los tests
        puedan cambiar la configuración con monkeypatch.
        """
        if _log_cfg.LOG_HEALTH:
            return False
        return (
            request.url.path in _log_cfg.LOG_SILENT_PATHS
            and response.status_code < 400
        )

    @staticmethod
    def _username_for_log(request: Request) -> str:
        token = request.cookies.get("ga_token", "")
        if token:
            # VERIFICANDO la firma, no leyendo los claims a pelo. Antes se usaba
            # `jwt.get_unverified_claims`, así que cualquiera podía mandar una
            # cookie fabricada con {"sub": "admin"} y aparecer como admin en el
            # registro. La petición se rechazaba igual —require_auth sí
            # comprueba la firma, no había escalada de privilegios— pero la
            # tabla que se consulta para investigar un incidente quedaba
            # sembrada con la identidad que el atacante eligiera.
            username = decode_token(token)
            if username:
                return username
            # Un token que no verifica es en sí mismo una señal: repetido desde
            # una misma IP es alguien probando. Antes se registraba como "-",
            # indistinguible de una petición anónima normal.
            return "?invalid"
        if request.cookies.get("ga_guest"):
            return "guest"
        return "-"
