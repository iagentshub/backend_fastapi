"""Rutas de memoria."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.config.data import MEMORY_DIR
from app.storage.storage import MemoryStorage

router = APIRouter(prefix="/api/memory", tags=["memory"])

_storage = MemoryStorage(MEMORY_DIR)


@router.get("")
async def list_memory(_: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    return _storage.list()


@router.get("/{filename}")
async def get_memory(
    filename: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    content = _storage.get(filename)
    if content is None:
        raise HTTPException(status_code=404, detail="Archivo de memoria no encontrado")
    return {"filename": filename, "content": content}


@router.post("/{filename}")
async def save_memory(
    filename: str, request: Request, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    body = await request.json()
    content = str(body.get("content") or "")
    return _storage.save(filename, content)


@router.delete("/{filename}")
async def delete_memory(
    filename: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    if not _storage.delete(filename):
        raise HTTPException(status_code=404, detail="Archivo de memoria no encontrado")
    return {"ok": True}
