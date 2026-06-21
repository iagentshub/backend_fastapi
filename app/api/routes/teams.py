"""Rutas de equipos — /api/teams."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.auth.auth import (
    demote_if_no_teams,
    get_user_by_username,
    get_user_role,
    promote_to_gestor,
    send_team_invitation_email,
)
from app.config.data import DB_FILE
from app.storage.guest import is_guest
from app.storage.teams import TeamStorage

router = APIRouter(prefix="/api/teams", tags=["teams"])

_ts = TeamStorage(DB_FILE)


# ── Dependencias locales ───────────────────────────────────────────────────────


def _require_team_manager(team_id: str, user: str = Depends(require_auth)) -> str:
    role = get_user_role(user)
    if role == "admin":
        return user
    if not _ts.is_manager(team_id, user):
        raise HTTPException(403, "Solo el gestor del equipo puede realizar esta acción")
    return user


def _require_team_member(team_id: str, user: str = Depends(require_auth)) -> str:
    role = get_user_role(user)
    if role == "admin":
        return user
    member = _ts.get_member(team_id, user)
    if not member:
        raise HTTPException(403, "No eres miembro de este equipo")
    return user


# ── Equipos ────────────────────────────────────────────────────────────────────


@router.get("/")
async def list_my_teams(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    return _ts.list_teams_for_user(user)


@router.post("/")
async def create_team(request: Request, user: str = Depends(require_auth)) -> Dict[str, Any]:
    if is_guest(user):
        raise HTTPException(403, "Los invitados no pueden crear equipos")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "El nombre del equipo es obligatorio")
    try:
        team = _ts.create_team(name, user)
    except ValueError as e:
        raise HTTPException(409, str(e))
    promote_to_gestor(user)
    return team


@router.get("/{team_id}")
async def get_team(team_id: str, user: str = Depends(_require_team_member)) -> Dict[str, Any]:
    team = _ts.get_team(team_id)
    if not team:
        raise HTTPException(404, "Equipo no encontrado")
    return team


@router.patch("/{team_id}")
async def rename_team(
    team_id: str, request: Request, user: str = Depends(_require_team_manager)
) -> Dict[str, Any]:
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "El nombre del equipo es obligatorio")
    _ts.rename_team(team_id, name)
    team = _ts.get_team(team_id)
    return team or {}


@router.delete("/{team_id}")
async def delete_team(
    team_id: str, user: str = Depends(_require_team_manager)
) -> Dict[str, Any]:
    team = _ts.get_team(team_id)
    if not team:
        raise HTTPException(404, "Equipo no encontrado")
    _ts.delete_team(team_id)
    demote_if_no_teams(team["created_by"])
    # Demote all managers that no longer manage any team
    for m in _ts.list_teams_for_user(team["created_by"]):
        pass  # listed above already handled
    return {"ok": True}


# ── Miembros ───────────────────────────────────────────────────────────────────


@router.get("/{team_id}/members")
async def list_members(
    team_id: str, user: str = Depends(_require_team_manager)
) -> List[Dict[str, Any]]:
    return _ts.list_members(team_id)


@router.patch("/{team_id}/members/{username}")
async def update_member(
    team_id: str,
    username: str,
    request: Request,
    user: str = Depends(_require_team_manager),
) -> Dict[str, Any]:
    member = _ts.get_member(team_id, username)
    if not member:
        raise HTTPException(404, "Miembro no encontrado")
    body = await request.json()
    if "permissions" in body:
        _ts.update_member_permissions(team_id, username, body["permissions"])
    if "is_manager" in body:
        _ts.update_member_role(team_id, username, bool(body["is_manager"]))
        # Sync role in users table
        if body["is_manager"]:
            promote_to_gestor(username)
        else:
            demote_if_no_teams(username)
    return _ts.get_member(team_id, username) or {}


@router.delete("/{team_id}/members/{username}")
async def remove_member(
    team_id: str,
    username: str,
    user: str = Depends(_require_team_manager),
) -> Dict[str, Any]:
    member = _ts.get_member(team_id, username)
    if not member:
        raise HTTPException(404, "Miembro no encontrado")
    if username == user and _ts.is_manager(team_id, username):
        managed = _ts.list_managed_teams(username)
        if len(managed) <= 1:
            raise HTTPException(400, "No puedes expulsarte si eres el único gestor del equipo")
    _ts.remove_member(team_id, username)
    demote_if_no_teams(username)
    return {"ok": True}


# ── Invitaciones ───────────────────────────────────────────────────────────────


@router.get("/{team_id}/invitations")
async def list_invitations(
    team_id: str, user: str = Depends(_require_team_manager)
) -> List[Dict[str, Any]]:
    return _ts.list_invitations(team_id)


@router.post("/{team_id}/invitations")
async def send_invitation(
    team_id: str,
    request: Request,
    user: str = Depends(_require_team_manager),
) -> Dict[str, Any]:
    team = _ts.get_team(team_id)
    if not team:
        raise HTTPException(404, "Equipo no encontrado")
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "El email es obligatorio")
    # Check if already a member
    target_user = get_user_by_username(email)
    if target_user and _ts.get_member(team_id, email):
        raise HTTPException(400, "Este usuario ya es miembro del equipo")
    token = _ts.create_invitation(team_id, email, user)
    base_url = str(request.base_url).rstrip("/")
    send_team_invitation_email(email, team["name"], user, token, base_url)
    return {"ok": True, "token": token}


@router.delete("/{team_id}/invitations/{token}")
async def cancel_invitation(
    team_id: str, token: str, user: str = Depends(_require_team_manager)
) -> Dict[str, Any]:
    inv = _ts.get_invitation(token)
    if not inv or inv.get("team_id") != team_id:
        raise HTTPException(404, "Invitación no encontrada")
    _ts.cancel_invitation(token)
    return {"ok": True}


# ── Invitaciones del usuario (cualquier equipo) ────────────────────────────────


@router.get("/invitations/pending")
async def my_pending_invitations(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    user_obj = get_user_by_username(user)
    if not user_obj:
        return []
    email = user_obj.get("email", user)
    return _ts.list_invitations_for_email(email)


@router.get("/invitations/received")
async def my_received_invitations(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    """All invitations received by the current user (all statuses)."""
    user_obj = get_user_by_username(user)
    if not user_obj:
        return []
    email = user_obj.get("email", user)
    return _ts.list_invitations_for_email_all(email)


@router.get("/invitations/sent")
async def my_sent_invitations(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    """All invitations sent by the current user."""
    return _ts.list_invitations_sent_by(user)


@router.post("/invitations/{token}/accept")
async def accept_invitation(
    token: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    return _handle_invitation_response(token, user, accept=True)


@router.post("/invitations/{token}/reject")
async def reject_invitation(
    token: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    return _handle_invitation_response(token, user, accept=False)


def _handle_invitation_response(token: str, user: str, *, accept: bool) -> Dict[str, Any]:
    if accept and is_guest(user):
        raise HTTPException(403, "Los invitados no pueden unirse a equipos")
    user_obj = get_user_by_username(user)
    if not user_obj:
        raise HTTPException(404, "Usuario no encontrado")
    email = user_obj.get("email", user)
    inv = _ts.get_invitation(token)
    if not inv:
        raise HTTPException(404, "Invitación no encontrada o ya procesada")
    if inv.get("invited_email") != email.lower():
        raise HTTPException(403, "Esta invitación no es para tu cuenta")
    result = _ts.consume_invitation(token, accept)
    if result is None:
        raise HTTPException(400, "La invitación ha expirado o ya fue procesada")
    if accept:
        _ts.add_member(result["team_id"], user, is_manager=False)
    return {"ok": True, "status": "accepted" if accept else "rejected"}
