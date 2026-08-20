"""Rutas de memoria."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.api.routes.auth import require_session
from app.config.data import MEMORY_DIR
from app.errors import APIError
from app.models.request_bodies import MemoryBody
from app.storage.memory_storage import MemoryStorage

router = APIRouter(prefix="/api/memory", tags=["memory"])

_storage = MemoryStorage(MEMORY_DIR)


@router.get("")
async def list_memory(user: str = Depends(require_session)) -> List[Dict[str, Any]]:
    return await _storage.list(owner_id=user)


@router.get("/{filename}")
async def get_memory(
    filename: str, user: str = Depends(require_session)
) -> Dict[str, Any]:
    content = await _storage.get(filename, owner_id=user)
    if content is None:
        raise APIError(
            404,
            "not_found",
            "Archivo de memoria no encontrado",
            extra={"resource": "memory_file"},
        )
    return {"filename": filename, "content": content}


@router.post("/{filename}")
async def save_memory(
    filename: str, body: MemoryBody, user: str = Depends(require_session)
) -> Dict[str, Any]:
    body = body.payload()
    content = str(body.get("content") or "")
    return await _storage.save(filename, content, owner_id=user)


@router.delete("/{filename}")
async def delete_memory(
    filename: str, user: str = Depends(require_session)
) -> Dict[str, Any]:
    if not await _storage.delete(filename, owner_id=user):
        raise APIError(
            404,
            "not_found",
            "Archivo de memoria no encontrado",
            extra={"resource": "memory_file"},
        )
    return {"ok": True}
