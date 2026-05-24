"""Rutas de skills."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_role
from app.config.data import DB_FILE, SKILLS_DIR
from app.storage.guest import get_session, is_guest
from app.storage.knowledge import FolderStorage
from app.storage.storage import SkillStorage
from app.storage.teams import TeamStorage

router = APIRouter(prefix="/api/skills", tags=["skills"])

_storage = SkillStorage(SKILLS_DIR)
_folders = FolderStorage(DB_FILE)
_ts = TeamStorage(DB_FILE)

_VALID_SCOPES = {"public", "private", "all"}


class SkillFolderMove(BaseModel):
    folder_id: Optional[str] = None


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise HTTPException(status_code=400, detail="Scope no válido")


@router.get("")
async def list_skills(
    scope: str = "all", user: str = Depends(require_auth)
) -> List[Dict[str, Any]]:
    _check_scope(scope)
    if is_guest(user):
        s = get_session(user)
        public = _storage.list("public") if scope in ("public", "all") else []
        private = s.skills if scope in ("private", "all") else []
        return public + private
    items = _storage.list(scope)
    if get_user_role(user) != "admin":
        # Filter: public skills + own private skills + legacy private (no owner_id)
        items = [
            s for s in items
            if s.get("scope") == "public"
            or s.get("owner_id") is None
            or s.get("owner_id") == user
        ]
        # Inject team-shared skills not already visible
        shared_ids = set(_ts.get_user_shared_resource_ids(user, "skill"))
        own_ids = {s["id"] for s in items}
        for sid in shared_ids - own_ids:
            sk = _storage.get_any(sid)
            if sk:
                sk["_shared"] = True
                items.append(sk)
    return items


@router.get("/{scope}/{skill_id}")
async def get_skill(
    scope: str, skill_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    _check_scope(scope)
    if is_guest(user) and scope == "private":
        sk = next((s for s in get_session(user).skills if s.get("id") == skill_id), None)
        if not sk:
            raise HTTPException(status_code=404, detail="Skill no encontrada")
        return sk
    sk = _storage.get(scope, skill_id)
    if not sk:
        raise HTTPException(status_code=404, detail="Skill no encontrada")
    return sk


@router.post("/{scope}")
async def save_skill(
    scope: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    _check_scope(scope)
    payload = await request.json()
    if is_guest(user):
        if scope == "public":
            raise HTTPException(status_code=403, detail="Los invitados no pueden modificar skills públicas")
        s = get_session(user)
        skill: Dict[str, Any] = {**payload, "id": payload.get("id") or uuid4().hex[:12], "scope": "private"}
        s.skills = [sk for sk in s.skills if sk.get("id") != skill["id"]]
        s.skills.append(skill)
        return skill
    try:
        return _storage.save(scope, payload, owner_id=user)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.patch("/private/{skill_id}/folder")
async def move_skill_folder(
    skill_id: str,
    body: SkillFolderMove,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden mover skills")
    if not _storage.move_folder(skill_id, body.folder_id):
        raise HTTPException(status_code=404, detail="Skill no encontrada")
    return {"ok": True}


@router.delete("/{scope}/{skill_id}")
async def delete_skill(
    scope: str, skill_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    _check_scope(scope)
    if is_guest(user):
        if scope == "public":
            raise HTTPException(status_code=403, detail="Los invitados no pueden eliminar skills públicas")
        s = get_session(user)
        before = len(s.skills)
        s.skills = [sk for sk in s.skills if sk.get("id") != skill_id]
        if len(s.skills) == before:
            raise HTTPException(status_code=404, detail="Skill no encontrada")
        return {"ok": True}
    try:
        if not _storage.delete(scope, skill_id):
            raise HTTPException(status_code=404, detail="Skill no encontrada")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}
