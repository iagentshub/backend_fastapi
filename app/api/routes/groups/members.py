"""Miembros de un grupo: alta, baja y cambio de rol."""


from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Depends

from app.api.routes.auth import GroupContext, require_group
from app.api.routes.groups._shared import (
    _assert_not_guest,
    _assert_not_personal_group,
    _groups,
    _nombre_visible,
    _validate_permissions,
    router,
)
from app.auth.auth import (
    get_user_by_id,
    get_user_by_username,
    get_user_role,
)
from app.errors import APIError
from app.models.request_bodies import (
    GROUP_ROLES,
    GroupMemberBody,
    GroupMemberUpdateBody,
)
from app.services.notifications import notify


@router.get("/{group_id}/members")
async def list_members(
    group_id: str,
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        user = await get_user_by_id(ctx.user)
        return [
            {
                "username": user["username"] if user else "",
                "role": "owner",
                "permissions": {},
            }
        ]
    if (
        not await _groups.can_access(group_id, ctx.user)
        and await get_user_role(ctx.user) != "admin"
    ):
        raise APIError(
            403, "forbidden", "Sin acceso a este grupo", extra={"resource": "group"}
        )
    return await _groups.list_members(group_id)

@router.post("/{group_id}/members")
async def add_member(
    group_id: str,
    body: GroupMemberBody,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    _assert_not_personal_group(group_id, ctx.user)
    # Sin `.lower()`, al contrario que la invitación. Da igual quién gane:
    # `get_user_by_username` normaliza con `normalize_username`, que ya baja a
    # minúsculas, así que las dos formas resuelven el mismo usuario.
    username = str(body.username or "").strip()
    role = str(body.role or "member").strip()
    if not username:
        raise APIError(
            400,
            "field_required",
            "El username es obligatorio",
            extra={"field": "username"},
        )
    if role not in GROUP_ROLES:
        raise APIError(400, "invalid_field", "Rol inválido", extra={"field": "role"})
    if (
        not await _groups.can_manage(group_id, ctx.user)
        and await get_user_role(ctx.user) != "admin"
    ):
        raise APIError(403, "forbidden", "Sin permisos para invitar miembros")
    target_user = await get_user_by_username(username)
    if not target_user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    grupo = await _groups.get(group_id)
    if not grupo:
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    await _groups.add_member(group_id, target_user["id"], role)
    await notify(
        user_id=target_user["id"],
        kind="group_member_added",
        actor=await _nombre_visible(ctx.user),
        group=grupo.get("name", ""),
    )
    return {"ok": True, "group_id": group_id, "username": username, "role": role}

@router.delete("/{group_id}/members/{username}")
async def remove_member(
    group_id: str,
    username: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    _assert_not_personal_group(group_id, ctx.user)
    target_user = await get_user_by_username(username)
    target_user_id = target_user["id"] if target_user else ""
    if (
        not await _groups.can_manage(group_id, ctx.user)
        and await get_user_role(ctx.user) != "admin"
    ):
        if target_user_id != ctx.user:
            raise APIError(403, "forbidden", "Sin permisos para eliminar miembros")
    group = await _groups.get(group_id)
    if not group:
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    if target_user_id == group["created_by"]:
        raise APIError(
            400, "cannot_remove_group_owner", "No puedes eliminar al creador del grupo"
        )
    if not target_user or not await _groups.remove_member(group_id, target_user_id):
        raise APIError(
            404, "not_found", "Miembro no encontrado", extra={"resource": "member"}
        )
    # Solo si te saca otro: esta misma ruta es la de darse de baja uno mismo
    # (ver la excepción de permisos de arriba), y avisarte de algo que acabas de
    # hacer tú es ruido.
    if target_user_id != ctx.user:
        await notify(
            user_id=target_user_id,
            kind="group_member_removed",
            actor=await _nombre_visible(ctx.user),
            group=group.get("name", ""),
        )
    return {"ok": True}

@router.patch("/{group_id}/members/{username}")
async def update_member_role(
    group_id: str,
    username: str,
    body: GroupMemberUpdateBody,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    _assert_not_personal_group(group_id, ctx.user)
    has_role = "role" in body.model_fields_set
    role = str(body.role or "").strip()
    permissions = body.permissions
    if not has_role and permissions is None:
        raise APIError(
            422, "role_or_permissions_required", "Rol o permisos obligatorios"
        )
    if has_role and role not in GROUP_ROLES:
        raise APIError(400, "invalid_field", "Rol inválido", extra={"field": "role"})
    if permissions is not None and not isinstance(permissions, dict):
        raise APIError(
            422, "invalid_field", "Permisos inválidos", extra={"field": "permissions"}
        )
    if permissions is not None:
        _validate_permissions(permissions)
    if (
        not await _groups.can_manage(group_id, ctx.user)
        and await get_user_role(ctx.user) != "admin"
    ):
        raise APIError(403, "forbidden", "Sin permisos para cambiar roles")
    target_user = await get_user_by_username(username)
    if not target_user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    target_user_id = target_user["id"]
    if has_role and not await _groups.update_member_role(
        group_id, target_user_id, role
    ):
        raise APIError(
            404, "not_found", "Miembro no encontrado", extra={"resource": "member"}
        )
    if permissions is not None and not await _groups.update_member_permissions(
        group_id, target_user_id, permissions
    ):
        raise APIError(
            404, "not_found", "Miembro no encontrado", extra={"resource": "member"}
        )
    # Solo el cambio de rol se avisa. Los permisos son un diccionario anidado
    # que no se resume en una frase, y un correo que diga «te han cambiado los
    # permisos» sin decir cuáles no ayuda a nadie.
    if has_role:
        grupo = await _groups.get(group_id)
        await notify(
            user_id=target_user_id,
            kind="group_role_changed",
            actor=await _nombre_visible(ctx.user),
            group=(grupo or {}).get("name", ""),
            role=role,
        )
    return {
        "ok": True,
        "group_id": group_id,
        "username": username,
        **({"role": role} if has_role else {}),
        **({"permissions": permissions} if permissions is not None else {}),
    }
