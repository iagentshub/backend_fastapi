"""API principal — GAIA Backend."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import jwt as _jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.routes import (
    accounts,
    agent_builder,
    agents,
    auth,
    billing,
    centinel,
    chats,
    connections,
    knowledge,
    logs,
    memory,
    resource_management,
    settings,
    sharing,
    skill_builder,
    skills,
    workspaces,
)
from app.api.routes.auth import admin_router, users_router
from app.api.routes.social import router as social_router
from app.auth.auth import ensure_admin_user, purge_expired_deletions
from app.config import data as _cfg
from app.config.cors import CORS_ORIGINS
from app.config.session import BODY_MAX_BYTES as _BODY_MAX_BYTES
from app.middleware.licenses import LicenseGateMiddleware
from app.middleware.locale import LocaleMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.storage.db import close_db_pool, init_db, open_db
from app.utils import flog
from app.utils.net import client_ip as _client_ip_util

_docs_url = (
    "/docs" if os.getenv("GAIA_DEV_MODE", "").lower() in ("1", "true", "yes") else None
)


class _BodySizeLimitMiddleware:
    """Middleware ASGI puro que limita el tamaño real del body (no solo Content-Length).

    A diferencia de BaseHTTPMiddleware, envuelve el callable `receive` del protocolo
    ASGI y cuenta los bytes reales según llegan, lo que impide el bypass vía
    Content-Length declarado falso sin romper endpoints SSE/streaming.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Rechazo rápido por Content-Length declarado (evita leer el body innecesariamente)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        cl_raw = headers.get(b"content-length")
        if cl_raw:
            try:
                if int(cl_raw) > _BODY_MAX_BYTES:
                    response = JSONResponse(
                        {
                            "detail": {
                                "code": "payload_too_large",
                                "message": "Payload demasiado grande (máx. 2 MB)",
                            }
                        },
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return
            except (ValueError, OverflowError):
                pass

        # Envolver receive para contar bytes reales chunk a chunk
        total_bytes = 0
        limit_exceeded = False

        async def limited_receive() -> dict:
            nonlocal total_bytes, limit_exceeded
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"")
                total_bytes += len(chunk)
                if total_bytes > _BODY_MAX_BYTES:
                    limit_exceeded = True
                    # Sustituir el body por vacío para que FastAPI no lo procese
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        await self.app(scope, limited_receive, send)

        # Si se superó el límite durante la lectura, el handler ya habrá respondido
        # o el error será capturado por FastAPI. El flag queda para depuración.


class _RequestLogger(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000

        # A3: usar client_ip() para respetar TRUSTED_PROXIES y evitar log injection
        ip = _client_ip_util(request) or "-"

        # Extraer usuario del JWT
        username = "-"
        try:
            token = request.cookies.get("ga_token", "")
            if token:
                payload = _jwt.get_unverified_claims(token)
                username = payload.get("sub") or payload.get("username") or "-"
            elif request.cookies.get("ga_guest"):
                username = "guest"
        except Exception:
            pass

        msg = (
            f"{request.method} {request.url.path} → {response.status_code} ({ms:.0f}ms)"
        )
        if response.status_code >= 500:
            flog.error(msg, ip=ip, username=username)
        elif response.status_code >= 400:
            flog.warning(msg, ip=ip, username=username)
        else:
            flog.info(msg, ip=ip, username=username)
        return response


async def _gdpr_purge_loop() -> None:
    """Purga cuentas con el período de gracia expirado cada 6 horas."""
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            n = await purge_expired_deletions()
            if n:
                flog.ok(f"[gdpr] {n} cuenta(s) eliminadas definitivamente")
        except Exception as exc:
            flog.error(f"[gdpr] Error en purga automática: {exc}")


async def _log_purge_loop() -> None:
    """Purga entradas de log antiguas cada 24 horas según la retención configurada."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            from app.api.routes.logs import purge_old_logs

            await purge_old_logs()
        except Exception as exc:
            flog.error(f"[logs] Error en purga automática: {exc}")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await init_db(_cfg.DB_FILE)
    await ensure_admin_user()
    purge_task = asyncio.create_task(_gdpr_purge_loop())
    log_purge_task = asyncio.create_task(_log_purge_loop())
    flog.ok("iAgents Hub arrancado")
    yield
    purge_task.cancel()
    log_purge_task.cancel()
    await close_db_pool()
    flog.info("iAgents Hub detenido")


def create_app() -> FastAPI:
    app = FastAPI(
        title="GAIA Backend",
        version="1.0.0",
        docs_url=_docs_url,
        redoc_url=None,
        lifespan=_lifespan,
    )

    app.add_middleware(_RequestLogger)
    app.add_middleware(_BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(LocaleMiddleware)
    app.add_middleware(LicenseGateMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ValueError)
    async def _value_error_handler(request, exc: ValueError):
        # Red de seguridad: convierte los ValueError de validación que se
        # escapan de una ruta (p. ej. una restricción de storage no capturada
        # localmente) en un 400 con {code, message} en vez de un 500 opaco.
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "code": "invalid_operation",
                    "message": str(exc) or "Operación no válida",
                }
            },
        )

    app.include_router(auth.router)
    app.include_router(admin_router)
    app.include_router(users_router)
    app.include_router(connections.router)
    app.include_router(agents.router)
    app.include_router(skills.router)
    app.include_router(memory.router)
    app.include_router(settings.router)
    app.include_router(accounts.router)
    app.include_router(chats.router)
    app.include_router(knowledge.router)
    app.include_router(logs.router)
    app.include_router(sharing.router)
    app.include_router(workspaces.router)
    app.include_router(billing.router)
    app.include_router(centinel.router)
    app.include_router(agent_builder.router)
    app.include_router(skill_builder.router)
    app.include_router(resource_management.router)
    app.include_router(social_router)

    @app.get("/api/health", tags=["health"])
    async def _health():
        try:
            async with open_db() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False
        status = "ok" if db_ok else "degraded"
        return {"status": status, "db": db_ok, "version": os.environ.get("GAIA_VERSION", "dev")}

    return app
