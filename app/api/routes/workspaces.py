"""Rutas de workspaces — CRUD y cambio de workspace activo."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request, Response

from app.api.routes.auth import WorkspaceContext, require_auth, require_workspace
from app.auth.auth import create_token, get_user_by_username, get_user_role
from app.config.data import DB_FILE
from app.config.session import SECURE_COOKIES
from app.errors import APIError
from app.storage.guest import is_guest
from app.storage.workspaces import WorkspaceStorage

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])

_ws = WorkspaceStorage(DB_FILE)
_PERMISSION_ACTIONS = {
    "agents": {"use"},
    "connections": {"direct", "via_agent"},
    "knowledge": {"view"},
}


def _assert_not_guest(user: str) -> None:
    if is_guest(user):
        raise APIError(403, "forbidden", "Los invitados no pueden gestionar workspaces")


def _validate_permissions(permissions: Dict[str, Any]) -> None:
    for section, config in permissions.items():
        allowed_actions = _PERMISSION_ACTIONS.get(section)
        if allowed_actions is None or not isinstance(config, dict):
            raise APIError(422, "invalid_field", "Permisos inválidos", extra={"field": "permissions"})
        if "default" in config and not isinstance(config["default"], bool):
            raise APIError(422, "invalid_field", "Permisos inválidos", extra={"field": "permissions"})
        items = config.get("items", {})
        if not isinstance(items, dict):
            raise APIError(422, "invalid_field", "Permisos inválidos", extra={"field": "permissions"})
        for resource_id, actions in items.items():
            if not resource_id or not isinstance(actions, dict):
                raise APIError(422, "invalid_field", "Permisos inválidos", extra={"field": "permissions"})
            if any(
                action not in allowed_actions or not isinstance(value, bool)
                for action, value in actions.items()
            ):
                raise APIError(422, "invalid_field", "Permisos inválidos", extra={"field": "permissions"})


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
        raise APIError(400, "field_required", "El nombre es obligatorio", extra={"field": "name"})
    if len(name) > 80:
        raise APIError(
            400, "name_too_long", "El nombre no puede superar los 80 caracteres",
            extra={"max_length": 80},
        )
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
        raise APIError(
            400, "personal_workspace_forbidden",
            "El workspace personal no se puede renombrar aquí",
            extra={"action": "rename"},
        )
    name = str(body.get("name") or "").strip()
    if not name:
        raise APIError(400, "field_required", "El nombre es obligatorio", extra={"field": "name"})
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos para modificar este workspace")
    updated = await _ws.update(workspace_id, name)
    if not updated:
        raise APIError(404, "not_found", "Workspace no encontrado", extra={"resource": "workspace"})
    return {"ok": True, "id": workspace_id, "name": name}


# ── Eliminar workspace ─────────────────────────────────────────────────────────

@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if workspace_id == ctx.user:
        raise APIError(
            400, "personal_workspace_forbidden",
            "No puedes eliminar tu workspace personal",
            extra={"action": "delete"},
        )
    ws = await _ws.get(workspace_id)
    if not ws:
        raise APIError(404, "not_found", "Workspace no encontrado", extra={"resource": "workspace"})
    if ws["created_by"] != ctx.user and await get_user_role(ctx.user) != "admin":
        raise APIError(
            403, "owner_only_action", "Solo el creador puede eliminar el workspace",
            extra={"action": "delete"},
        )
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
        raise APIError(
            400, "personal_workspace_forbidden",
            "El workspace personal no se puede desactivar",
            extra={"action": "disable"},
        )
    body = await request.json()
    status = str(body.get("status") or "").strip()
    if status not in ("active", "disabled"):
        raise APIError(
            422, "invalid_field", "status debe ser 'active' o 'disabled'",
            extra={"field": "status"},
        )
    ws = await _ws.get(workspace_id)
    if not ws:
        raise APIError(404, "not_found", "Workspace no encontrado", extra={"resource": "workspace"})
    if ws["created_by"] != ctx.user and await get_user_role(ctx.user) != "admin":
        raise APIError(
            403, "owner_only_action", "Solo el propietario puede cambiar el estado del workspace",
            extra={"action": "status"},
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
        raise APIError(403, "forbidden", "Sin acceso a este workspace", extra={"resource": "workspace"})
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
        raise APIError(400, "field_required", "El username es obligatorio", extra={"field": "username"})
    if role not in ("owner", "admin", "member"):
        raise APIError(400, "invalid_field", "Rol inválido", extra={"field": "role"})
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos para invitar miembros")
    if not await get_user_by_username(username):
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    if not await _ws.get(workspace_id):
        raise APIError(404, "not_found", "Workspace no encontrado", extra={"resource": "workspace"})
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
            raise APIError(403, "forbidden", "Sin permisos para eliminar miembros")
    ws = await _ws.get(workspace_id)
    if not ws:
        raise APIError(404, "not_found", "Workspace no encontrado", extra={"resource": "workspace"})
    if username == ws["created_by"]:
        raise APIError(400, "cannot_remove_workspace_owner", "No puedes eliminar al creador del workspace")
    if not await _ws.remove_member(workspace_id, username):
        raise APIError(404, "not_found", "Miembro no encontrado", extra={"resource": "member"})
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
        raise APIError(422, "role_or_permissions_required", "Rol o permisos obligatorios")
    if has_role and role not in ("owner", "admin", "member"):
        raise APIError(400, "invalid_field", "Rol inválido", extra={"field": "role"})
    if permissions is not None and not isinstance(permissions, dict):
        raise APIError(422, "invalid_field", "Permisos inválidos", extra={"field": "permissions"})
    if permissions is not None:
        _validate_permissions(permissions)
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos para cambiar roles")
    if has_role and not await _ws.update_member_role(workspace_id, username, role):
        raise APIError(404, "not_found", "Miembro no encontrado", extra={"resource": "member"})
    if permissions is not None and not await _ws.update_member_permissions(
        workspace_id, username, permissions
    ):
        raise APIError(404, "not_found", "Miembro no encontrado", extra={"resource": "member"})
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
        raise APIError(404, "not_found", "Invitacion no encontrada", extra={"resource": "invitation"})
    return {"ok": True, "workspace_id": workspace_id}


@router.post("/invitations/{inv_id}/reject")
async def reject_invitation(
    inv_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not await _ws.reject_invitation(inv_id, ctx.user):
        raise APIError(404, "not_found", "Invitacion no encontrada", extra={"resource": "invitation"})
    return {"ok": True}


@router.get("/{workspace_id}/invitations")
async def list_workspace_invitations(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos")
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
        raise APIError(400, "field_required", "El username es obligatorio", extra={"field": "username"})
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos para invitar miembros")
    if not await _ws.get(workspace_id):
        raise APIError(404, "not_found", "Workspace no encontrado", extra={"resource": "workspace"})
    if not await get_user_by_username(username):
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    if await _ws.is_member(workspace_id, username):
        raise APIError(409, "already_member", "El usuario ya es miembro de este workspace")
    inv = await _ws.invite_user(workspace_id, username, ctx.user)
    if inv is None:
        raise APIError(
            409, "already_exists", "Ya existe una invitacion pendiente para este usuario",
            extra={"resource": "invitation"},
        )
    return inv


@router.delete("/{workspace_id}/invitations/{inv_id}")
async def cancel_workspace_invitation(
    workspace_id: str,
    inv_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not await _ws.can_manage(workspace_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos")
    if not await _ws.cancel_invitation(inv_id, workspace_id):
        raise APIError(404, "not_found", "Invitacion no encontrada", extra={"resource": "invitation"})
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
        raise APIError(
            400, "field_required", "Se requiere 'username' del nuevo propietario",
            extra={"field": "username"},
        )
    if new_owner == username:
        raise APIError(400, "already_owner", "Ya eres el propietario")
    ws = await _ws.get(workspace_id)
    if not ws:
        raise APIError(404, "not_found", "Workspace no encontrado", extra={"resource": "workspace"})
    if ws.get("created_by") != username and await get_user_role(username) != "admin":
        raise APIError(
            403, "owner_only_action", "Solo el propietario puede transferir el workspace",
            extra={"action": "transfer"},
        )
    if not await _ws.transfer_ownership(workspace_id, new_owner):
        raise APIError(400, "not_a_member", "El usuario no es miembro de este workspace")
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
        raise APIError(403, "workspace_unavailable", "Workspace no disponible o desactivado")
    if not await _ws.is_member(workspace_id, username):
        raise APIError(403, "not_a_member", "No eres miembro de este workspace")
    token = create_token(username, workspace_id=workspace_id)
    response.set_cookie(
        "ga_token", token, httponly=True, samesite="lax",
        secure=SECURE_COOKIES, max_age=43200,  # A1: flag Secure, A2: 12h = mismo que login
    )
    return {"ok": True, "workspace_id": workspace_id}
