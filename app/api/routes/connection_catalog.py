"""Catálogo de proveedores y descubrimiento de modelos de conexión."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.api.routes.auth import require_session
from app.connections import all_providers
from app.models.request_bodies import OllamaModelsBody

router = APIRouter(prefix="/api/connections", tags=["connection-catalog"])


@router.get("/providers")
async def list_providers(_: str = Depends(require_session)) -> list[dict[str, Any]]:
    return all_providers()


@router.post("/ollama-models")
async def ollama_models(
    body: OllamaModelsBody,
    _: str = Depends(require_session),
) -> dict[str, Any]:
    from app.connections.ollama import OllamaProvider

    payload = body.payload()
    host = (payload.get("host") or "http://localhost:11434").strip().rstrip("/")
    api_key = str(payload.get("api_key") or "").strip()
    try:
        config = {"host": host, "api_key": api_key}
        OllamaProvider.validate_config(config, purpose="models")
        models = await asyncio.to_thread(OllamaProvider.fetch_models, config)
        return {"models": models}
    except Exception as exc:  # noqa: BLE001
        # El motivo se devuelve en la respuesta ({'models': [], 'error': …}),
        # que es justo lo que el diálogo de conexión muestra al usuario.
        return {"models": [], "error": str(exc)}
