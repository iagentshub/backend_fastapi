"""API principal — GAIA Backend."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.auth import ensure_admin_user
from app.config.cors import CORS_ORIGINS
from app.api.routes import auth, connections, agents, skills, memory, settings, accounts, chats, knowledge
from app.api.routes.auth import admin_router
from app.middleware.locale import LocaleMiddleware


@asynccontextmanager
async def _lifespan(app: FastAPI):
    ensure_admin_user()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="GAIA Backend", version="1.0.0", docs_url="/docs", redoc_url=None, lifespan=_lifespan)

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
    app.include_router(connections.router)
    app.include_router(agents.router)
    app.include_router(skills.router)
    app.include_router(memory.router)
    app.include_router(settings.router)
    app.include_router(accounts.router)
    app.include_router(chats.router)
    app.include_router(knowledge.router)

    return app
