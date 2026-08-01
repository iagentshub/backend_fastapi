"""Rutas de memoria."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from app.api.routes.auth import require_auth
from app.config.data import MEMORY_DIR
from app.errors import APIError
from app.storage.guest import get_session, is_guest
from app.storage.storage import MemoryStorage

router = APIRouter(prefix="/api/memory", tags=["memory"])

_storage = MemoryStorage(MEMORY_DIR)


@router.get("")
async def list_memory(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    if is_guest(user):
        s = get_session(user)
        return [
            {"filename": k, "size": len(v), "updated_at": None}
            for k, v in s.memory.items()
        ]
    return await _storage.list(owner_id=user)


@router.get("/{filename}")
async def get_memory(
    filename: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        content = get_session(user).memory.get(filename)
        if content is None:
            raise APIError(
                404, "not_found", "Archivo de memoria no encontrado",
                extra={"resource": "memory_file"},
            )
        return {"filename": filename, "content": content}
    content = await _storage.get(filename, owner_id=user)
    if content is None:
        raise APIError(
            404, "not_found", "Archivo de memoria no encontrado",
            extra={"resource": "memory_file"},
        )
    return {"filename": filename, "content": content}


@router.post("/{filename}")
async def save_memory(
    filename: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    body = await request.json()
    content = str(body.get("content") or "")
    if is_guest(user):
        get_session(user).memory[filename] = content
        return {"filename": filename}
    return await _storage.save(filename, content, owner_id=user)


@router.delete("/{filename}")
async def delete_memory(
    filename: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        s = get_session(user)
        if filename not in s.memory:
            raise APIError(
                404, "not_found", "Archivo de memoria no encontrado",
                extra={"resource": "memory_file"},
            )
        del s.memory[filename]
        return {"ok": True}
    if not await _storage.delete(filename, owner_id=user):
        raise APIError(
            404, "not_found", "Archivo de memoria no encontrado",
            extra={"resource": "memory_file"},
        )
    return {"ok": True}
