"""Catálogo de proveedores y descubrimiento de modelos de conexión."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.api.routes.auth import require_auth
from app.config.security import assert_safe_url
from app.connections import all_providers
from app.models.request_bodies import OllamaModelsBody

router = APIRouter(prefix="/api/connections", tags=["connection-catalog"])


@router.get("/providers")
async def list_providers(_: str = Depends(require_auth)) -> list[dict[str, Any]]:
    return all_providers()


@router.post("/ollama-models")
async def ollama_models(
    body: OllamaModelsBody,
    _: str = Depends(require_auth),
) -> dict[str, Any]:
    from app.connections.ollama import OllamaProvider

    payload = body.payload()
    host = (payload.get("host") or "http://localhost:11434").strip().rstrip("/")
    api_key = str(payload.get("api_key") or "").strip()
    try:
        assert_safe_url(host)
        data = await asyncio.to_thread(OllamaProvider._fetch_tags, host, api_key)
        models = [m["name"] for m in (data.get("models") or []) if m.get("name")]
        return {"models": models}
    except Exception as exc:
        return {"models": [], "error": str(exc)}
