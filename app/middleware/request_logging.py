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
        username = self._username_for_log(request)
        ip = client_ip(request) or "-"
        token = flog.set_request_context(ip=ip, username=username)
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            flog.error(
                f"{request.method} {request.url.path} → 500 ({elapsed_ms:.0f}ms)",
                exc_info=True,
            )
            raise
        finally:
            flog.reset_request_context(token)

        if self._silenciar(request, response):
            return response

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        message = (
            f"{request.method} {request.url.path} → {response.status_code} "
            f"({elapsed_ms:.0f}ms)"
        )
        context = {"ip": ip, "username": username}
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
        # Aquí se leía una cookie `ga_guest` para registrar al invitado como
        # "guest". Nunca existió: ningún emisor la puso jamás, y el invitado
        # viaja en `ga_token` como todo el mundo — así que sale arriba, y con su
        # id, que distingue a un invitado de otro.
        return "-"
