"""Rutas de skills."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.config.data import SKILLS_DIR
from app.storage.storage import SkillStorage

router = APIRouter(prefix="/api/skills", tags=["skills"])

_storage = SkillStorage(SKILLS_DIR)


@router.get("")
async def list_skills(
    scope: str = "all", _: str = Depends(require_auth)
) -> List[Dict[str, Any]]:
    return _storage.list(scope)


@router.get("/{scope}/{skill_id}")
async def get_skill(
    scope: str, skill_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    sk = _storage.get(scope, skill_id)
    if not sk:
        raise HTTPException(status_code=404, detail="Skill no encontrada")
    return sk


@router.post("/{scope}")
async def save_skill(
    scope: str, request: Request, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return _storage.save(scope, payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/{scope}/{skill_id}")
async def delete_skill(
    scope: str, skill_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    try:
        if not _storage.delete(scope, skill_id):
            raise HTTPException(status_code=404, detail="Skill no encontrada")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}
