"""API principal — GAIA Backend."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    accounts,
    agent_builder,
    agents,
    auth,
    billing,
    centinel,
    chats,
    connections,
    explore,
    groups,
    knowledge,
    labels,
    logs,
    memory,
    prompts,
    resource_linking,
    resource_management,
    settings,
    sharing,
    skill_builder,
    skills,
    social,
)
from app.api.routes.admin import admin_router
from app.api.routes.users import users_router
from app.auth.auth import ensure_admin_user, purge_expired_deletions
from app.config import data as _cfg
from app.config.cors import CORS_ORIGINS
from app.middleware.body_limit import BodySizeLimitMiddleware
from app.middleware.licenses import LicenseGateMiddleware
from app.middleware.locale import LocaleMiddleware
from app.middleware.request_logging import RequestLoggerMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.storage.db import close_db_pool, init_db, open_db
from app.utils import flog


def _dev_mode() -> bool:
    """Se lee en cada create_app() para que los tests puedan cambiarla."""
    return os.getenv("GAIA_DEV_MODE", "").lower() in ("1", "true", "yes")


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
    tasks = (
        asyncio.create_task(_gdpr_purge_loop(), name="gdpr-purge"),
        asyncio.create_task(_log_purge_loop(), name="log-purge"),
    )
    flog.ok("iAgents Hub arrancado")
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_db_pool()
        flog.info("iAgents Hub detenido")


def create_app() -> FastAPI:
    # El esquema describe la superficie entera de la API: fuera de desarrollo se
    # cierra junto con /docs, que sin él no sirve de nada.
    dev = _dev_mode()
    app = FastAPI(
        title="GAIA Backend",
        version="1.0.0",
        docs_url="/docs" if dev else None,
        openapi_url="/openapi.json" if dev else None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    app.add_middleware(RequestLoggerMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
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
        # Red de seguridad para ValueError que se escapan de una ruta sin
        # captura local (siempre un bug, no validación de usuario — esa ya
        # se convierte a APIError en cada endpoint). Se registra con
        # traceback completo como antes (nivel error, no warning) para no
        # perder visibilidad, y se devuelve un mensaje genérico al cliente
        # en vez del texto crudo de la excepción, que puede contener detalle
        # interno (rutas, nombres de columna, etc.) no pensado para salir de
        # los logs del servidor.
        flog.error(
            f"ValueError no capturado en {request.method} {request.url.path}: {exc}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "code": "invalid_operation",
                    "message": "Operación no válida",
                }
            },
        )

    app.include_router(auth.router)
    app.include_router(admin_router)
    app.include_router(users_router)
    app.include_router(connections.router)
    app.include_router(agents.router)
    app.include_router(skills.router)
    app.include_router(prompts.router)
    app.include_router(memory.router)
    app.include_router(settings.router)
    app.include_router(accounts.router)
    app.include_router(chats.router)
    app.include_router(knowledge.router)
    app.include_router(labels.router)
    app.include_router(logs.router)
    app.include_router(sharing.router)
    app.include_router(groups.router)
    app.include_router(billing.router)
    app.include_router(centinel.router)
    app.include_router(agent_builder.router)
    app.include_router(skill_builder.router)
    app.include_router(resource_management.router)
    app.include_router(social.router)
    app.include_router(explore.router)
    app.include_router(resource_linking.router)

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
