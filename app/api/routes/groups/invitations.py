"""Invitaciones: las que recibe un usuario y las que emite un grupo."""


from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Depends

from app.api.routes.auth import GroupContext, require_group
from app.api.routes.groups._shared import (
    _assert_not_guest,
    _assert_not_personal_group,
    _groups,
    _nombre_visible,
    router,
)
from app.auth.auth import (
    get_user_by_username,
    get_user_role,
)
from app.errors import APIError
from app.services.notifications import notify


@router.get("/my-invitations")
async def my_invitations(
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
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
        raise APIError(
            404,
            "not_found",
            "Invitacion no encontrada",
            extra={"resource": "invitation"},
        )
    return {"ok": True, "group_id": group_id}

@router.post("/invitations/{inv_id}/reject")
async def reject_invitation(
    inv_id: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not await _groups.reject_invitation(inv_id, ctx.user):
        raise APIError(
            404,
            "not_found",
            "Invitacion no encontrada",
            extra={"resource": "invitation"},
        )
    return {"ok": True}

@router.get("/{group_id}/invitations")
async def list_group_invitations(
    group_id: str,
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        return []
    if (
        not await _groups.can_manage(group_id, ctx.user)
        and await get_user_role(ctx.user) != "admin"
    ):
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
        raise APIError(
            400,
            "field_required",
            "El username es obligatorio",
            extra={"field": "username"},
        )
    if (
        not await _groups.can_manage(group_id, ctx.user)
        and await get_user_role(ctx.user) != "admin"
    ):
        raise APIError(403, "forbidden", "Sin permisos para invitar miembros")
    grupo = await _groups.get(group_id)
    if not grupo:
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    target_user = await get_user_by_username(username)
    if not target_user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    target_user_id = target_user["id"]
    if await _groups.is_member(group_id, target_user_id):
        raise APIError(409, "already_member", "El usuario ya es miembro de este grupo")
    inv = await _groups.invite_user(group_id, target_user_id, ctx.user)
    if inv is None:
        raise APIError(
            409,
            "already_exists",
            "Ya existe una invitacion pendiente para este usuario",
            extra={"resource": "invitation"},
        )
    inv["username"] = username
    # Hasta aquí la invitación era solo una fila y el invitado únicamente se
    # enteraba si entraba a mirar su perfil. `notify` no lanza, así que esto no
    # puede convertir una invitación creada en un error para quien invita.
    await notify(
        user_id=target_user_id,
        kind="group_invite",
        actor=await _nombre_visible(ctx.user),
        group=grupo.get("name", ""),
        invitation_id=inv["id"],
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
    if (
        not await _groups.can_manage(group_id, ctx.user)
        and await get_user_role(ctx.user) != "admin"
    ):
        raise APIError(403, "forbidden", "Sin permisos")
    if not await _groups.cancel_invitation(inv_id, group_id):
        raise APIError(
            404,
            "not_found",
            "Invitacion no encontrada",
            extra={"resource": "invitation"},
        )
    return {"ok": True}
