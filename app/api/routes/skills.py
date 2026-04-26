"""Rutas de skills."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.config.data import SKILLS_DIR
from app.storage.storage import SkillStorage

router = APIRouter(prefix="/api/skills", tags=["skills"])

_storage = SkillStorage(SKILLS_DIR)

_VALID_SCOPES = {"public", "private", "all"}


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise HTTPException(status_code=400, detail="Scope no válido")


@router.get("")
async def list_skills(
    scope: str = "all", _: str = Depends(require_auth)
) -> List[Dict[str, Any]]:
    _check_scope(scope)
    return _storage.list(scope)


@router.get("/{scope}/{skill_id}")
async def get_skill(
    scope: str, skill_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    _check_scope(scope)
    sk = _storage.get(scope, skill_id)
    if not sk:
        raise HTTPException(status_code=404, detail="Skill no encontrada")
    return sk


@router.post("/{scope}")
async def save_skill(
    scope: str, request: Request, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    _check_scope(scope)
    payload = await request.json()
    try:
        return _storage.save(scope, payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/{scope}/{skill_id}")
async def delete_skill(
    scope: str, skill_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    _check_scope(scope)
    try:
        if not _storage.delete(scope, skill_id):
            raise HTTPException(status_code=404, detail="Skill no encontrada")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}
