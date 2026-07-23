"""Rutas de memoria."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.config.data import MEMORY_DIR
from app.storage.guest import get_session, is_guest
from app.storage.folders import FolderStorage
from app.storage.storage import MemoryStorage

router = APIRouter(prefix="/api/memory", tags=["memory"])

_storage = MemoryStorage(MEMORY_DIR)
_folders = FolderStorage()


@router.get("")
async def list_memory(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    if is_guest(user):
        s = get_session(user)
        return [
            {"filename": k, "size": len(v), "updated_at": None}
            for k, v in s.memory.items()
        ]
    return await _folders.enrich(await _storage.list(owner_id=user), user, "memory")


@router.get("/{filename}")
async def get_memory(
    filename: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        content = get_session(user).memory.get(filename)
        if content is None:
            raise HTTPException(
                status_code=404, detail="Archivo de memoria no encontrado"
            )
        return {"filename": filename, "content": content}
    content = await _storage.get(filename, owner_id=user)
    if content is None:
        raise HTTPException(status_code=404, detail="Archivo de memoria no encontrado")
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


@router.patch("/{filename}")
async def patch_memory(
    filename: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    body = await request.json()
    if "folder_id" in body and not is_guest(user):
        try:
            await _folders.assign(
                user, "memory", filename,
                str(body["folder_id"]) if body.get("folder_id") else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "folder_id": body.get("folder_id")}


@router.delete("/{filename}")
async def delete_memory(
    filename: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        s = get_session(user)
        if filename not in s.memory:
            raise HTTPException(
                status_code=404, detail="Archivo de memoria no encontrado"
            )
        del s.memory[filename]
        return {"ok": True}
    if not await _storage.delete(filename, owner_id=user):
        raise HTTPException(status_code=404, detail="Archivo de memoria no encontrado")
    await _folders.remove_resource(user, "memory", filename)
    return {"ok": True}
