"""API principal — GAIA Backend."""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt as _jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils import flog
from app.auth.auth import ensure_admin_user, purge_expired_deletions
from app.config.cors import CORS_ORIGINS
from app.config import data as _cfg
from app.config.session import BODY_MAX_BYTES as _BODY_MAX_BYTES
from app.storage.db import init_db, close_db_pool, open_db
from app.api.routes import auth, connections, agents, skills, memory, settings, accounts, chats, knowledge, logs, sharing, workspaces, groups
from app.api.routes.auth import admin_router, users_router
from app.api.routes.social import router as social_router
from app.middleware.locale import LocaleMiddleware
from app.middleware.security import SecurityHeadersMiddleware

_docs_url = "/docs" if os.getenv("GAIA_DEV_MODE", "").lower() in ("1", "true", "yes") else None


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        cl = request.headers.get("content-length")
        if cl and int(cl) > _BODY_MAX_BYTES:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"detail": "Payload demasiado grande (máx. 2 MB)"},
                status_code=413,
            )
        return await call_next(request)


class _RequestLogger(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000

        user = "-"
        try:
            token = request.cookies.get("ga_token", "")
            if token:
                payload = _jwt.get_unverified_claims(token)
                user = payload.get("sub") or payload.get("username") or "-"
        except Exception:
            pass

        msg = f"{request.method} {request.url.path} → {response.status_code} ({ms:.0f}ms) user={user}"
        if response.status_code >= 500:
            flog.error(msg)
        elif response.status_code >= 400:
            flog.warning(msg)
        else:
            flog.info(msg)
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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await init_db(_cfg.DB_FILE)
    await ensure_admin_user()
    purge_task = asyncio.create_task(_gdpr_purge_loop())
    flog.ok("iAgents Hub arrancado")
    yield
    purge_task.cancel()
    await close_db_pool()
    flog.info("iAgents Hub detenido")


def create_app() -> FastAPI:
    app = FastAPI(title="GAIA Backend", version="1.0.0", docs_url=_docs_url, redoc_url=None, lifespan=_lifespan)

    app.add_middleware(_RequestLogger)
    app.add_middleware(_BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(LocaleMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
    app.include_router(groups.router)
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
        return {"status": status, "db": db_ok}

    return app
