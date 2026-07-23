"""Rutas de workspaces — CRUD y cambio de workspace activo."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.routes.auth import WorkspaceContext, require_auth, require_workspace
from app.auth.auth import create_token, get_user_by_username, get_user_role
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
    team_workspaces = await _ws.list_for_user(ctx.user)
    personal_ws = {
        "id": ctx.user,
        "name": "Personal",
        "type": "personal",
        "role": "owner",
        "active": ctx.workspace_id == ctx.user,
    }
    team_list = [
        {**ws, "type": "team", "active": ws["id"] == ctx.workspace_id}
        for ws in team_workspaces
    ]
    return [personal_ws] + team_list


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
    ws = await _ws.create(name, created_by=ctx.user)
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
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos para modificar este workspace")
    updated = await _ws.update(workspace_id, name)
    if not updated:
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
    ws = await _ws.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if ws["created_by"] != ctx.user and await get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Solo el creador puede eliminar el workspace")
    await _ws.delete(workspace_id)
    return {"ok": True}


# ── Desactivar / reactivar workspace (propietario) ──────────────────────────────

@router.post("/{workspace_id}/status")
async def set_workspace_status(
    workspace_id: str,
    request: Request,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if workspace_id == ctx.user:
        raise HTTPException(status_code=400, detail="El workspace personal no se puede desactivar")
    body = await request.json()
    status = str(body.get("status") or "").strip()
    if status not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="status debe ser 'active' o 'disabled'")
    ws = await _ws.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if ws["created_by"] != ctx.user and await get_user_role(ctx.user) != "admin":
        raise HTTPException(
            status_code=403, detail="Solo el propietario puede cambiar el estado del workspace"
        )
    await _ws.set_status(workspace_id, status)
    return {"ok": True, "status": status}


# ── Miembros ───────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/members")
async def list_members(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    if not await _ws.can_access(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin acceso a este workspace")
    return await _ws.list_members(workspace_id)


@router.post("/{workspace_id}/members")
async def add_member(
    workspace_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    username = str(body.get("username") or "").strip()
    role = str(body.get("role") or "member").strip()
    if not username:
        raise HTTPException(status_code=400, detail="El username es obligatorio")
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="Rol inválido")
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos para invitar miembros")
    if not await get_user_by_username(username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not await _ws.get(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    await _ws.add_member(workspace_id, username, role)
    return {"ok": True, "workspace_id": workspace_id, "username": username, "role": role}


@router.delete("/{workspace_id}/members/{username}")
async def remove_member(
    workspace_id: str,
    username: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        if username != ctx.user:
            raise HTTPException(status_code=403, detail="Sin permisos para eliminar miembros")
    ws = await _ws.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if username == ws["created_by"]:
        raise HTTPException(status_code=400, detail="No puedes eliminar al creador del workspace")
    if not await _ws.remove_member(workspace_id, username):
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
    has_role = "role" in body
    role = str(body.get("role") or "").strip()
    permissions = body.get("permissions")
    if not has_role and permissions is None:
        raise HTTPException(status_code=422, detail="Rol o permisos obligatorios")
    if has_role and role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="Rol inválido")
    if permissions is not None and not isinstance(permissions, dict):
        raise HTTPException(status_code=422, detail="Permisos inválidos")
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos para cambiar roles")
    if has_role and not await _ws.update_member_role(workspace_id, username, role):
        raise HTTPException(status_code=404, detail="Miembro no encontrado")
    if permissions is not None and not await _ws.update_member_permissions(
        workspace_id, username, permissions
    ):
        raise HTTPException(status_code=404, detail="Miembro no encontrado")
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "username": username,
        **({"role": role} if has_role else {}),
        **({"permissions": permissions} if permissions is not None else {}),
    }


# ── Invitaciones ───────────────────────────────────────────────────────────────

@router.get("/my-invitations")
async def my_invitations(ctx: WorkspaceContext = Depends(require_workspace)) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    return await _ws.list_my_invitations(ctx.user)


@router.post("/invitations/{inv_id}/accept")
async def accept_invitation(
    inv_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    workspace_id = await _ws.accept_invitation(inv_id, ctx.user)
    if not workspace_id:
        raise HTTPException(status_code=404, detail="Invitacion no encontrada")
    return {"ok": True, "workspace_id": workspace_id}


@router.post("/invitations/{inv_id}/reject")
async def reject_invitation(
    inv_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not await _ws.reject_invitation(inv_id, ctx.user):
        raise HTTPException(status_code=404, detail="Invitacion no encontrada")
    return {"ok": True}


@router.get("/{workspace_id}/invitations")
async def list_workspace_invitations(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos")
    return await _ws.list_invitations(workspace_id)


@router.post("/{workspace_id}/invitations")
async def invite_member(
    workspace_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    username = str(body.get("username") or "").strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="El username es obligatorio")
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos para invitar miembros")
    if not await _ws.get(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if not await get_user_by_username(username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if await _ws.is_member(workspace_id, username):
        raise HTTPException(status_code=409, detail="El usuario ya es miembro de este workspace")
    inv = await _ws.invite_user(workspace_id, username, ctx.user)
    if inv is None:
        raise HTTPException(status_code=409, detail="Ya existe una invitacion pendiente para este usuario")
    return inv


@router.delete("/{workspace_id}/invitations/{inv_id}")
async def cancel_workspace_invitation(
    workspace_id: str,
    inv_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos")
    if not await _ws.cancel_invitation(inv_id, workspace_id):
        raise HTTPException(status_code=404, detail="Invitacion no encontrada")
    return {"ok": True}


@router.post("/{workspace_id}/transfer-ownership")
async def transfer_workspace_ownership(
    workspace_id: str,
    request: Request,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Transfiere la propiedad del workspace a otro miembro existente."""
    body = await request.json()
    new_owner = str(body.get("username", "")).strip()
    if not new_owner:
        raise HTTPException(status_code=400, detail="Se requiere 'username' del nuevo propietario")
    if new_owner == username:
        raise HTTPException(status_code=400, detail="Ya eres el propietario")
    ws = await _ws.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if ws.get("created_by") != username and await get_user_role(username) != "admin":
        raise HTTPException(status_code=403, detail="Solo el propietario puede transferir el workspace")
    if not await _ws.transfer_ownership(workspace_id, new_owner):
        raise HTTPException(status_code=400, detail="El usuario no es miembro de este workspace")
    return {"ok": True}


# ── Cambio de workspace activo ─────────────────────────────────────────────────

@router.post("/switch/{workspace_id}")
async def switch_workspace(
    workspace_id: str,
    response: Response,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Cambia el workspace activo del usuario y emite un nuevo token.

    Devuelve 403 si el workspace está desactivado o el usuario no es miembro.
    """
    _assert_not_guest(username)

    # Cambio al workspace personal propio: siempre permitido
    if workspace_id == username:
        token = create_token(username, workspace_id=username)
        response.set_cookie(
            "ga_token", token, httponly=True, samesite="lax",
            secure=SECURE_COOKIES, max_age=43200,  # A1: flag Secure, A2: 12h = mismo que login
        )
        return {"ok": True, "workspace_id": workspace_id}

    # Workspace de equipo: debe estar activo y el usuario debe ser miembro
    ws = await _ws.get(workspace_id)
    if not ws or ws.get("status", "active") != "active":
        raise HTTPException(status_code=403, detail="Workspace no disponible o desactivado")
    if not await _ws.is_member(workspace_id, username):
        raise HTTPException(status_code=403, detail="No eres miembro de este workspace")
    token = create_token(username, workspace_id=workspace_id)
    response.set_cookie(
        "ga_token", token, httponly=True, samesite="lax",
        secure=SECURE_COOKIES, max_age=43200,  # A1: flag Secure, A2: 12h = mismo que login
    )
    return {"ok": True, "workspace_id": workspace_id}

