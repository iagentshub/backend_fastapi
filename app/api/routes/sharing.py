"""Rutas de compartición de recursos con equipos — /api/sharing."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, DB_FILE, SKILLS_DIR
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import AgentStorage, ConnectionStorage, SkillStorage
from app.storage.teams import TeamStorage

router = APIRouter(prefix="/api/sharing", tags=["sharing"])

_ts = TeamStorage(DB_FILE)
_agents = AgentStorage(AGENTS_DIR)
_connections = ConnectionStorage(DB_FILE)
_knowledge = KnowledgeStorage(DB_FILE)
_skills = SkillStorage(SKILLS_DIR)

_VALID_TYPES = {"agent", "connection", "knowledge", "skill"}


def _assert_valid_type(resource_type: str) -> None:
    if resource_type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Tipo no válido: {resource_type}")


def _assert_owner(resource_type: str, resource_id: str, user: str) -> None:
    """Raise 403 if user does not own the resource."""
    role = get_user_role(user)
    if role == "admin":
        return
    if resource_type == "agent":
        a = _agents.get(resource_id)
        if not a or a.get("owner_id") != user:
            raise HTTPException(status_code=403, detail="No eres propietario de este recurso")
    elif resource_type == "connection":
        c = _connections.get(resource_id, owner_id=user)
        if not c:
            raise HTTPException(status_code=403, detail="No eres propietario de este recurso")
    elif resource_type == "knowledge":
        k = _knowledge.get(resource_id, owner_id=user)
        if not k:
            raise HTTPException(status_code=403, detail="No eres propietario de este recurso")
    elif resource_type == "skill":
        sk = _skills.get_any(resource_id)
        if not sk:
            raise HTTPException(status_code=404, detail="Skill no encontrada")
        if sk.get("owner_id") != user:
            raise HTTPException(status_code=403, detail="No eres propietario de este recurso")


def _assert_member(team_id: str, user: str) -> None:
    member = _ts.get_member(team_id, user)
    if not member and get_user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="No perteneces a este equipo")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/{resource_type}/{resource_id}")
async def get_resource_sharing(
    resource_type: str, resource_id: str, user: str = Depends(require_auth)
) -> List[Dict[str, Any]]:
    """Equipos con los que está compartido un recurso."""
    _assert_valid_type(resource_type)
    return _ts.get_resource_teams(resource_type, resource_id)


@router.post("/{resource_type}/{resource_id}")
async def share_resource(
    resource_type: str, resource_id: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Compartir un recurso con un equipo."""
    _assert_valid_type(resource_type)
    body = await request.json()
    team_id = str(body.get("team_id") or "").strip()
    if not team_id:
        raise HTTPException(status_code=422, detail="team_id requerido")
    _assert_owner(resource_type, resource_id, user)
    _assert_member(team_id, user)
    _ts.share_resource(resource_type, resource_id, team_id, user)
    return {"ok": True}


@router.delete("/{resource_type}/{resource_id}/{team_id}")
async def unshare_resource(
    resource_type: str, resource_id: str, team_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Dejar de compartir un recurso con un equipo."""
    _assert_valid_type(resource_type)
    _assert_owner(resource_type, resource_id, user)
    _ts.unshare_resource(resource_type, resource_id, team_id)
    return {"ok": True}


@router.get("/by-team/{team_id}/{resource_type}")
async def get_team_resources(
    team_id: str, resource_type: str, user: str = Depends(require_auth)
) -> List[Dict[str, Any]]:
    """Recursos compartidos con un equipo (para el directorio de grupo)."""
    _assert_valid_type(resource_type)
    _assert_member(team_id, user)
    return _ts.get_team_shared_resources(team_id, resource_type)
