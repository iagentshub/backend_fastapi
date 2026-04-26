"""API principal — GAIA Backend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.auth import ensure_admin_password_hashed
from app.config.cors import CORS_ORIGINS
from app.api.routes import auth, connections, agents, skills, memory
from app.api.routes.auth import admin_router


def create_app() -> FastAPI:
    ensure_admin_password_hashed()
    app = FastAPI(title="GAIA Backend", version="1.0.0", docs_url="/docs", redoc_url=None)

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(auth.router)
    app.include_router(admin_router)
    app.include_router(connections.router)
    app.include_router(agents.router)
    app.include_router(skills.router)
    app.include_router(memory.router)

    return app
