"""API principal — GAIA Backend."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    accounts,
    agent_builder,
    agent_chat,
    agent_exports,
    agent_preferences,
    agents,
    auth,
    billing,
    centinel,
    chats,
    connection_catalog,
    connection_diagnostics,
    connection_sync,
    connections,
    explore,
    groups,
    knowledge,
    labels,
    llm_orchestrations,
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
    tools,
    users,
)
from app.api.routes.admin import admin_router
from app.auth.auth import ensure_admin_user
from app.auth.gdpr import purge_expired_deletions
from app.config import data as _cfg
from app.config.cors import CORS_ORIGINS
from app.middleware.body_limit import BodySizeLimitMiddleware
from app.middleware.licenses import LicenseGateMiddleware
from app.middleware.locale import LocaleMiddleware
from app.middleware.request_logging import RequestLoggerMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.pagination.http import PAGINATION_HEADERS
from app.services.llm_executor import shutdown_llm_executor
from app.services.workflow_run_executor import (
    stop_workflow_runs,
    workflow_run_maintenance_loop,
)
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
        asyncio.create_task(
            workflow_run_maintenance_loop(), name="workflow-run-maintenance"
        ),
    )
    flog.ok("iAgents Hub arrancado")
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stop_workflow_runs()
        shutdown_llm_executor()
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
    app.add_middleware(
        BodySizeLimitMiddleware,
        overrides={"/api/auth/me/avatar": 11 * 1024 * 1024},
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(LocaleMiddleware)
    app.add_middleware(LicenseGateMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Metadatos de páginas offset y cursor accesibles desde Flutter Web.
        expose_headers=PAGINATION_HEADERS,
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

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request, exc: RequestValidationError):
        # El tercer formato de error que nadie eligió: Pydantic responde
        # {"detail": [{type, loc, msg, input}]}, una lista donde el resto de la
        # API pone el objeto {code, message} que el cliente sabe traducir. Con
        # la lista, el cliente Flutter cae a su fallback y el usuario ve
        # "Error 422" sin decirle qué campo falla.
        primero = (exc.errors() or [{}])[0]
        if primero.get("type") in {"json_invalid", "model_attributes_type"} or (
            primero.get("type") == "missing" and primero.get("loc") == ("body",)
        ):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": {
                        "code": "invalid_json",
                        "message": "El cuerpo debe ser un objeto JSON válido",
                    }
                },
            )
        # loc[0] es siempre el origen ("body", "query", "path"); el nombre del
        # campo empieza en el segundo elemento.
        campo = ".".join(str(p) for p in primero.get("loc", [])[1:])
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "invalid_field",
                    "message": f"Campo inválido: {campo}"
                    if campo
                    else "Petición inválida",
                    "field": campo,
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request, exc: Exception):
        # Red de seguridad final: cualquier excepción no controlada en
        # cualquier ruta (p.ej. fallos de BD dentro de require_auth) queda
        # registrada con traceback completo y responde con el contrato JSON
        # estándar de la API, en vez del 500 en texto plano de Starlette que
        # además nunca pasaba por flog/app_logs porque RequestLoggerMiddleware
        # solo loguea si call_next() llega a devolver una respuesta.
        flog.error(
            f"Excepción no capturada en {request.method} {request.url.path}: {exc}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "internal_error",
                    "message": "Error interno del servidor.",
                }
            },
        )

    app.include_router(auth.router)
    app.include_router(admin_router)
    app.include_router(users.router)
    app.include_router(connection_catalog.router)
    app.include_router(connection_diagnostics.router)
    app.include_router(connection_sync.router)
    app.include_router(connections.router)
    app.include_router(agent_chat.router)
    app.include_router(agent_exports.router)
    app.include_router(agent_preferences.router)
    app.include_router(agents.router)
    app.include_router(skills.router)
    app.include_router(prompts.router)
    app.include_router(tools.router)
    app.include_router(memory.router)
    app.include_router(settings.router)
    app.include_router(accounts.router)
    app.include_router(chats.router)
    app.include_router(knowledge.router)
    app.include_router(labels.router)
    app.include_router(logs.router)
    app.include_router(llm_orchestrations.router)
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
        except Exception as exc:
            db_ok = False
            # El fallo no dejaba rastro: except pelado y ni una línea de log.
            flog.error(f"[health] la BD no responde: {exc}")
        cuerpo = {
            "status": "ok" if db_ok else "degraded",
            "db": db_ok,
            "version": os.environ.get("GAIA_VERSION", "dev"),
        }
        # Un balanceador, el HEALTHCHECK de Docker o una sonda de Kubernetes
        # leen el CÓDIGO, no el cuerpo: con un 200 el nodo seguía en el pool
        # recibiendo tráfico que no podía atender. Sin BD este proceso no sirve
        # para nada, así que que lo saquen.
        return JSONResponse(cuerpo, status_code=200 if db_ok else 503)

    return app
