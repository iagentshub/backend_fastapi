"""API principal — GAIA Backend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.login_local import init_local_admin, router as local_router
from app.auth.login_google import router as google_router
from app.config.cors import CORS_ORIGINS
from app.api.routes import auth, connections, agents, skills, memory
from app.api.routes.auth import admin_router


def create_app() -> FastAPI:
    init_local_admin()

    app = FastAPI(title="GAIA Backend", version="1.0.0", docs_url="/docs", redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(admin_router)
    app.include_router(local_router)
    app.include_router(google_router)
    app.include_router(connections.router)
    app.include_router(agents.router)
    app.include_router(skills.router)
    app.include_router(memory.router)

    return app
