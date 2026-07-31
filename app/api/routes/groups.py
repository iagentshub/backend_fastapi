"""Rutas de groups — CRUD y cambio de group activo."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request, Response

from app.api.routes.auth import GroupContext, require_auth, require_group
from app.auth.auth import create_token, get_user_by_username, get_user_role
from app.config.data import DB_FILE
from app.config.session import SECURE_COOKIES
from app.errors import APIError
from app.storage.groups import GroupStorage
from app.storage.guest import is_guest

router = APIRouter(prefix="/api/groups", tags=["groups"])

_groups = GroupStorage(DB_FILE)
_PERMISSION_ACTIONS = {
    "agents": {"use"},
    "connections": {"direct", "via_agent"},
    "knowledge": {"view"},
}


def _assert_not_guest(user: str) -> None:
    if is_guest(user):
        raise APIError(403, "forbidden", "Los invitados no pueden gestionar grupos")


def _assert_not_personal_group(group_id: str, username: str) -> None:
    if group_id == username:
        raise APIError(
            400,
            "personal_group_single_user",
            "El grupo Personal solo puede contener a su propietario",
            extra={"resource": "group"},
        )


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


# ── Listar groups del usuario ──────────────────────────────────────────────

@router.get("")
async def list_groups(ctx: GroupContext = Depends(require_group)) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    team_groups = await _groups.list_for_user(ctx.user)
    personal_groups = {
        "id": ctx.user,
        "name": "Personal",
        "type": "personal",
        "role": "owner",
        "active": ctx.group_id == ctx.user,
    }
    team_list = [
        {**group, "type": "team", "active": group["id"] == ctx.group_id}
        for group in team_groups
    ]
    return [personal_groups] + team_list


# ── Crear group de equipo ──────────────────────────────────────────────────

@router.post("")
async def create_group(
    body: Dict[str, Any],
    ctx: GroupContext = Depends(require_group),
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
    group = await _groups.create(name, created_by=ctx.user)
    return {**group, "type": "team", "active": False}


# ── Renombrar group ────────────────────────────────────────────────────────

@router.patch("/{group_id}")
async def update_group(
    group_id: str,
    body: Dict[str, Any],
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        raise APIError(400, "personal_group_forbidden", "El grupo Personal no se puede renombrar")
    name = str(body.get("name") or "").strip()
    if not name:
        raise APIError(400, "field_required", "El nombre es obligatorio", extra={"field": "name"})
    if not await _groups.can_manage(group_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos para modificar este grupo")
    updated = await _groups.update(group_id, name)
    if not updated:
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    return {"ok": True, "id": group_id, "name": name}


# ── Eliminar group ─────────────────────────────────────────────────────────

@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        raise APIError(400, "personal_group_forbidden", "No puedes eliminar el grupo Personal")
    group = await _groups.get(group_id)
    if not group:
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    if group["created_by"] != ctx.user and await get_user_role(ctx.user) != "admin":
        raise APIError(
            403, "owner_only_action", "Solo el creador puede eliminar el grupo",
            extra={"action": "delete"},
        )
    await _groups.delete(group_id)
    return {"ok": True}


# ── Desactivar / reactivar group (propietario) ──────────────────────────────

@router.post("/{group_id}/status")
async def set_group_status(
    group_id: str,
    request: Request,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        raise APIError(400, "personal_group_forbidden", "El grupo Personal no se puede desactivar")
    body = await request.json()
    status = str(body.get("status") or "").strip()
    if status not in ("active", "disabled"):
        raise APIError(
            422, "invalid_field", "status debe ser 'active' o 'disabled'",
            extra={"field": "status"},
        )
    group = await _groups.get(group_id)
    if not group:
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    if group["created_by"] != ctx.user and await get_user_role(ctx.user) != "admin":
        raise APIError(
            403, "owner_only_action", "Solo el propietario puede cambiar el estado del grupo",
            extra={"action": "status"},
        )
    await _groups.set_status(group_id, status)
    return {"ok": True, "status": status}


# ── Miembros ───────────────────────────────────────────────────────────────────

@router.get("/{group_id}/members")
async def list_members(
    group_id: str,
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        return [{"username": ctx.user, "role": "owner", "permissions": {}}]
    if not await _groups.can_access(group_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin acceso a este grupo", extra={"resource": "group"})
    return await _groups.list_members(group_id)


@router.post("/{group_id}/members")
async def add_member(
    group_id: str,
    body: Dict[str, Any],
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    _assert_not_personal_group(group_id, ctx.user)
    username = str(body.get("username") or "").strip()
    role = str(body.get("role") or "member").strip()
    if not username:
        raise APIError(400, "field_required", "El username es obligatorio", extra={"field": "username"})
    if role not in ("owner", "admin", "member"):
        raise APIError(400, "invalid_field", "Rol inválido", extra={"field": "role"})
    if not await _groups.can_manage(group_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos para invitar miembros")
    if not await get_user_by_username(username):
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    if not await _groups.get(group_id):
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    await _groups.add_member(group_id, username, role)
    return {"ok": True, "group_id": group_id, "username": username, "role": role}


@router.delete("/{group_id}/members/{username}")
async def remove_member(
    group_id: str,
    username: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    _assert_not_personal_group(group_id, ctx.user)
    if not await _groups.can_manage(group_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        if username != ctx.user:
            raise APIError(403, "forbidden", "Sin permisos para eliminar miembros")
    group = await _groups.get(group_id)
    if not group:
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    if username == group["created_by"]:
        raise APIError(400, "cannot_remove_group_owner", "No puedes eliminar al creador del grupo")
    if not await _groups.remove_member(group_id, username):
        raise APIError(404, "not_found", "Miembro no encontrado", extra={"resource": "member"})
    return {"ok": True}


@router.patch("/{group_id}/members/{username}")
async def update_member_role(
    group_id: str,
    username: str,
    body: Dict[str, Any],
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    _assert_not_personal_group(group_id, ctx.user)
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
    if not await _groups.can_manage(group_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos para cambiar roles")
    if has_role and not await _groups.update_member_role(group_id, username, role):
        raise APIError(404, "not_found", "Miembro no encontrado", extra={"resource": "member"})
    if permissions is not None and not await _groups.update_member_permissions(
        group_id, username, permissions
    ):
        raise APIError(404, "not_found", "Miembro no encontrado", extra={"resource": "member"})
    return {
        "ok": True,
        "group_id": group_id,
        "username": username,
        **({"role": role} if has_role else {}),
        **({"permissions": permissions} if permissions is not None else {}),
    }


# ── Invitaciones ───────────────────────────────────────────────────────────────

@router.get("/my-invitations")
async def my_invitations(ctx: GroupContext = Depends(require_group)) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    return await _groups.list_my_invitations(ctx.user)


@router.post("/invitations/{inv_id}/accept")
async def accept_invitation(
    inv_id: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    group_id = await _groups.accept_invitation(inv_id, ctx.user)
    if not group_id:
        raise APIError(404, "not_found", "Invitacion no encontrada", extra={"resource": "invitation"})
    return {"ok": True, "group_id": group_id}


@router.post("/invitations/{inv_id}/reject")
async def reject_invitation(
    inv_id: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not await _groups.reject_invitation(inv_id, ctx.user):
        raise APIError(404, "not_found", "Invitacion no encontrada", extra={"resource": "invitation"})
    return {"ok": True}


@router.get("/{group_id}/invitations")
async def list_group_invitations(
    group_id: str,
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        return []
    if not await _groups.can_manage(group_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos")
    return await _groups.list_invitations(group_id)


@router.post("/{group_id}/invitations")
async def invite_member(
    group_id: str,
    body: Dict[str, Any],
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    _assert_not_personal_group(group_id, ctx.user)
    username = str(body.get("username") or "").strip().lower()
    if not username:
        raise APIError(400, "field_required", "El username es obligatorio", extra={"field": "username"})
    if not await _groups.can_manage(group_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos para invitar miembros")
    if not await _groups.get(group_id):
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    if not await get_user_by_username(username):
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    if await _groups.is_member(group_id, username):
        raise APIError(409, "already_member", "El usuario ya es miembro de este grupo")
    inv = await _groups.invite_user(group_id, username, ctx.user)
    if inv is None:
        raise APIError(
            409, "already_exists", "Ya existe una invitacion pendiente para este usuario",
            extra={"resource": "invitation"},
        )
    return inv


@router.delete("/{group_id}/invitations/{inv_id}")
async def cancel_group_invitation(
    group_id: str,
    inv_id: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    _assert_not_personal_group(group_id, ctx.user)
    if not await _groups.can_manage(group_id, ctx.user) and await get_user_role(ctx.user) != "admin":
        raise APIError(403, "forbidden", "Sin permisos")
    if not await _groups.cancel_invitation(inv_id, group_id):
        raise APIError(404, "not_found", "Invitacion no encontrada", extra={"resource": "invitation"})
    return {"ok": True}


@router.post("/{group_id}/transfer-ownership")
async def transfer_group_ownership(
    group_id: str,
    request: Request,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Transfiere la propiedad del grupo a otro miembro existente."""
    _assert_not_personal_group(group_id, username)
    body = await request.json()
    new_owner = str(body.get("username", "")).strip()
    if not new_owner:
        raise APIError(
            400, "field_required", "Se requiere 'username' del nuevo propietario",
            extra={"field": "username"},
        )
    if new_owner == username:
        raise APIError(400, "already_owner", "Ya eres el propietario")
    group = await _groups.get(group_id)
    if not group:
        raise APIError(404, "not_found", "Grupo no encontrado", extra={"resource": "group"})
    if group.get("created_by") != username and await get_user_role(username) != "admin":
        raise APIError(
            403, "owner_only_action", "Solo el propietario puede transferir el grupo",
            extra={"action": "transfer"},
        )
    if not await _groups.transfer_ownership(group_id, new_owner):
        raise APIError(400, "not_a_member", "El usuario no es miembro de este grupo")
    return {"ok": True}


# ── Cambio de group activo ─────────────────────────────────────────────────

@router.post("/switch/{group_id}")
async def switch_group(
    group_id: str,
    response: Response,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Cambia el group activo del usuario y emite un nuevo token.

    Devuelve 403 si el group está desactivado o el usuario no es miembro.
    """
    _assert_not_guest(username)

    # Cambio al group personal propio: siempre permitido
    if group_id == username:
        token = create_token(username, group_id=username)
        response.set_cookie(
            "ga_token", token, httponly=True, samesite="lax",
            secure=SECURE_COOKIES, max_age=43200,  # A1: flag Secure, A2: 12h = mismo que login
        )
        return {"ok": True, "group_id": group_id}

    # Group de equipo: debe estar activo y el usuario debe ser miembro
    group = await _groups.get(group_id)
    if not group or group.get("status", "active") != "active":
        raise APIError(403, "group_unavailable", "Grupo no disponible o desactivado")
    if not await _groups.is_member(group_id, username):
        raise APIError(403, "not_a_member", "No eres miembro de este grupo")
    token = create_token(username, group_id=group_id)
    response.set_cookie(
        "ga_token", token, httponly=True, samesite="lax",
        secure=SECURE_COOKIES, max_age=43200,  # A1: flag Secure, A2: 12h = mismo que login
    )
    return {"ok": True, "group_id": group_id}
