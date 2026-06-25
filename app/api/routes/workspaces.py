"""Rutas de workspaces — CRUD y cambio de workspace activo."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.routes.auth import WorkspaceContext, require_auth, require_workspace
from app.auth.auth import create_token, get_user_role
from app.config.data import DB_FILE
from app.config.session import SECURE_COOKIES
from app.storage.guest import is_guest
from app.storage.workspaces import WorkspaceStorage

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])

_ws = WorkspaceStorage(DB_FILE)


def _assert_not_guest(user: str) -> None:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden gestionar workspaces")


# ── Listar workspaces del usuario ──────────────────────────────────────────────

@router.get("")
async def list_workspaces(ctx: WorkspaceContext = Depends(require_workspace)) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    team_workspaces = _ws.list_for_user(ctx.user)
    return [
        {
            "id": ctx.user,
            "name": "Personal",
            "type": "personal",
            "role": "owner",
            "active": ctx.workspace_id == ctx.user,
        },
        *[
            {**ws, "type": "team", "active": ctx.workspace_id == ws["id"]}
            for ws in team_workspaces
        ],
    ]


# ── Crear workspace de equipo ──────────────────────────────────────────────────

@router.post("")
async def create_workspace(
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    if len(name) > 80:
        raise HTTPException(status_code=400, detail="El nombre no puede superar los 80 caracteres")
    ws = _ws.create(name, created_by=ctx.user)
    return {**ws, "type": "team", "active": False}


# ── Renombrar workspace ────────────────────────────────────────────────────────

@router.patch("/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if workspace_id == ctx.user:
        raise HTTPException(status_code=400, detail="El workspace personal no se puede renombrar aquí")
    if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos para modificar este workspace")
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    if not _ws.update(workspace_id, name):
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    return {"ok": True, "id": workspace_id, "name": name}


# ── Eliminar workspace ─────────────────────────────────────────────────────────

@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if workspace_id == ctx.user:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu workspace personal")
    ws = _ws.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if ws["created_by"] != ctx.user and get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Solo el creador puede eliminar el workspace")
    _ws.delete(workspace_id)
    return {"ok": True}


# ── Cambiar workspace activo ───────────────────────────────────────────────────

@router.post("/switch/{workspace_id}")
async def switch_workspace(
    workspace_id: str,
    response: Response,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    user = ctx.user
    role = get_user_role(user)
    if workspace_id != user and not _ws.can_access(workspace_id, user) and role != "admin":
        raise HTTPException(status_code=403, detail="No tienes acceso a este workspace")
    token = create_token(user, workspace_id=workspace_id)
    response.set_cookie(
        "ga_token", token, httponly=True, samesite="lax",
        secure=SECURE_COOKIES, max_age=43200,
    )
    return {"ok": True, "workspace_id": workspace_id}


# ── Miembros ───────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/members")
async def list_members(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    if not _ws.can_access(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin acceso a este workspace")
    return _ws.list_members(workspace_id)


@router.post("/{workspace_id}/members")
async def add_member(
    workspace_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos para invitar miembros")
    username = str(body.get("username") or "").strip()
    role = str(body.get("role") or "member").strip()
    if not username:
        raise HTTPException(status_code=400, detail="El username es obligatorio")
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="Rol inválido")
    from app.auth.auth import get_user_by_username
    if not get_user_by_username(username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not _ws.get(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    _ws.add_member(workspace_id, username, role)
    return {"ok": True, "workspace_id": workspace_id, "username": username, "role": role}


@router.delete("/{workspace_id}/members/{username}")
async def remove_member(
    workspace_id: str,
    username: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
        if username != ctx.user:
            raise HTTPException(status_code=403, detail="Sin permisos para eliminar miembros")
    ws = _ws.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if username == ws["created_by"]:
        raise HTTPException(status_code=400, detail="No puedes eliminar al creador del workspace")
    if not _ws.remove_member(workspace_id, username):
        raise HTTPException(status_code=404, detail="Miembro no encontrado")
    return {"ok": True}


@router.patch("/{workspace_id}/members/{username}")
async def update_member_role(
    workspace_id: str,
    username: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos para cambiar roles")
    role = str(body.get("role") or "").strip()
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="Rol inválido")
    if not _ws.update_member_role(workspace_id, username, role):
        raise HTTPException(status_code=404, detail="Miembro no encontrado")
    return {"ok": True, "workspace_id": workspace_id, "username": username, "role": role}
