"""Logging HTTP ASGI con duración real, IP confiable y usuario de sesión."""

from __future__ import annotations

import asyncio
import time

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.passwords import decode_token
from app.config import logging as _log_cfg
from app.utils import flog
from app.utils.net import client_ip


class RequestLoggerMiddleware:
    """Atribuye y registra el ciclo completo sin intermediar el cuerpo HTTP.

    El estado se captura en ``http.response.start`` y la duración visible para
    el cliente termina en el último body/trailer. Esperar a ``self.app`` sigue
    siendo necesario para conservar el contexto durante cleanup/background,
    pero ese trabajo posterior no infla la duración HTTP registrada.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        started_at = time.perf_counter()
        username = self._username_for_log(request)
        ip = client_ip(request) or "-"
        status_code: int | None = None
        first_byte_at: float | None = None
        completed_at: float | None = None
        bytes_sent = 0
        expecting_trailers = False

        async def observed_send(message: Message) -> None:
            nonlocal status_code, first_byte_at, completed_at
            nonlocal bytes_sent, expecting_trailers

            message_type = message["type"]
            if message_type == "http.response.start":
                status_code = message["status"]
                expecting_trailers = bool(message.get("trailers", False))
            await send(message)

            if message_type == "http.response.start":
                first_byte_at = time.perf_counter()
            elif message_type == "http.response.body":
                bytes_sent += len(message.get("body", b""))
                if not message.get("more_body", False) and not expecting_trailers:
                    completed_at = time.perf_counter()
            elif message_type == "http.response.trailers" and not message.get(
                "more_trailers", False
            ):
                completed_at = time.perf_counter()

        context_token = flog.set_request_context(ip=ip, username=username)
        try:
            try:
                await self.app(scope, receive, observed_send)
            except asyncio.CancelledError:
                self._write_log(
                    request=request,
                    status_code=status_code,
                    started_at=started_at,
                    first_byte_at=first_byte_at,
                    completed_at=completed_at,
                    bytes_sent=bytes_sent,
                    outcome="cancelled",
                    ip=ip,
                    username=username,
                )
                raise
            except Exception:
                self._write_log(
                    request=request,
                    status_code=status_code,
                    started_at=started_at,
                    first_byte_at=first_byte_at,
                    completed_at=completed_at,
                    bytes_sent=bytes_sent,
                    outcome="failed",
                    ip=ip,
                    username=username,
                    exc_info=True,
                )
                raise
            else:
                self._write_log(
                    request=request,
                    status_code=status_code,
                    started_at=started_at,
                    first_byte_at=first_byte_at,
                    completed_at=completed_at,
                    bytes_sent=bytes_sent,
                    outcome="completed",
                    ip=ip,
                    username=username,
                )
        finally:
            flog.reset_request_context(context_token)

    @classmethod
    def _write_log(
        cls,
        *,
        request: Request,
        status_code: int | None,
        started_at: float,
        first_byte_at: float | None,
        completed_at: float | None,
        bytes_sent: int,
        outcome: str,
        ip: str,
        username: str,
        exc_info: bool = False,
    ) -> None:
        effective_status = status_code if status_code is not None else 500
        if outcome == "completed" and cls._silenciar(request, effective_status):
            return

        finished_at = completed_at or time.perf_counter()
        elapsed_ms = (finished_at - started_at) * 1000
        ttfb = (
            f"; ttfb={(first_byte_at - started_at) * 1000:.0f}ms"
            if first_byte_at is not None
            else ""
        )
        result = (
            str(effective_status)
            if outcome == "completed"
            else f"{outcome} status={status_code or '-'}"
        )
        message = (
            f"{request.method} {request.url.path} → {result} "
            f"({elapsed_ms:.0f}ms{ttfb}; bytes={bytes_sent})"
        )
        context = {"ip": ip, "username": username}
        if outcome == "failed":
            flog.error(message, exc_info=exc_info, **context)
        elif outcome == "cancelled":
            flog.warning(message, **context)
        elif effective_status >= 500:
            flog.error(message, **context)
        elif effective_status >= 400:
            flog.warning(message, **context)
        else:
            flog.info(message, **context)

    @staticmethod
    def _silenciar(request: Request, status_code: int) -> bool:
        """Silencia sondas de vida correctas; sus fallos siempre se registran."""
        if _log_cfg.LOG_HEALTH:
            return False
        return request.url.path in _log_cfg.LOG_SILENT_PATHS and status_code < 400

    @staticmethod
    def _username_for_log(request: Request) -> str:
        token = request.cookies.get("ga_token", "")
        if token:
            # La identidad del log se verifica igual que la de autorización.
            username = decode_token(token)
            if username:
                return username
            return "?invalid"
        return "-"
