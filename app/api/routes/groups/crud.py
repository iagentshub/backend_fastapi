"""Alta, edición, borrado y cambio de grupo activo."""


from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Cookie, Depends, Response

from app.api.routes.auth import GroupContext, require_auth, require_group
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
from app.auth.passwords import decode_claims
from app.auth.sessions import reissue_access
from app.errors import APIError
from app.models.request_bodies import StatusBody, UsernameBody
from app.services.notifications import notify


@router.get("")
async def list_groups(
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
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

@router.post("")
async def create_group(
    body: Dict[str, Any],
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    name = str(body.get("name") or "").strip()
    if not name:
        raise APIError(
            400, "field_required", "El nombre es obligatorio", extra={"field": "name"}
        )
    if len(name) > 80:
        raise APIError(
            400,
            "name_too_long",
            "El nombre no puede superar los 80 caracteres",
            extra={"max_length": 80},
        )
    group = await _groups.create(name, created_by=ctx.user)
    return {**group, "type": "team", "active": False}

@router.patch("/{group_id}")
async def update_group(
    group_id: str,
    body: Dict[str, Any],
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        raise APIError(
            400, "personal_group_forbidden", "El grupo Personal no se puede renombrar"
        )
    name = str(body.get("name") or "").strip()
    if not name:
        raise APIError(
            400, "field_required", "El nombre es obligatorio", extra={"field": "name"}
        )
    if (
        not await _groups.can_manage(group_id, ctx.user)
        and await get_user_role(ctx.user) != "admin"
    ):
        raise APIError(403, "forbidden", "Sin permisos para modificar este grupo")
    updated = await _groups.update(group_id, name)
    if not updated:
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    return {"ok": True, "id": group_id, "name": name}

@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        raise APIError(
            400, "personal_group_forbidden", "No puedes eliminar el grupo Personal"
        )
    group = await _groups.get(group_id)
    if not group:
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    if group["created_by"] != ctx.user and await get_user_role(ctx.user) != "admin":
        raise APIError(
            403,
            "owner_only_action",
            "Solo el creador puede eliminar el grupo",
            extra={"action": "delete"},
        )
    await _groups.delete(group_id)
    return {"ok": True}

@router.post("/{group_id}/status")
async def set_group_status(
    group_id: str,
    body: StatusBody,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if group_id == ctx.user:
        raise APIError(
            400, "personal_group_forbidden", "El grupo Personal no se puede desactivar"
        )
    body = body.payload()
    status = str(body.get("status") or "").strip()
    if status not in ("active", "disabled"):
        raise APIError(
            422,
            "invalid_field",
            "status debe ser 'active' o 'disabled'",
            extra={"field": "status"},
        )
    group = await _groups.get(group_id)
    if not group:
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    if group["created_by"] != ctx.user and await get_user_role(ctx.user) != "admin":
        raise APIError(
            403,
            "owner_only_action",
            "Solo el propietario puede cambiar el estado del grupo",
            extra={"action": "status"},
        )
    await _groups.set_status(group_id, status)
    return {"ok": True, "status": status}

@router.post("/{group_id}/transfer-ownership")
async def transfer_group_ownership(
    group_id: str,
    body: UsernameBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Transfiere la propiedad del grupo a otro miembro existente."""
    _assert_not_personal_group(group_id, username)
    body = body.payload()
    new_owner = str(body.get("username", "")).strip()
    if not new_owner:
        raise APIError(
            400,
            "field_required",
            "Se requiere 'username' del nuevo propietario",
            extra={"field": "username"},
        )
    target_user = await get_user_by_username(new_owner)
    if not target_user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    new_owner_id = target_user["id"]
    if new_owner_id == username:
        raise APIError(400, "already_owner", "Ya eres el propietario")
    group = await _groups.get(group_id)
    if not group:
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    if group.get("created_by") != username and await get_user_role(username) != "admin":
        raise APIError(
            403,
            "owner_only_action",
            "Solo el propietario puede transferir el grupo",
            extra={"action": "transfer"},
        )
    if not await _groups.transfer_ownership(group_id, new_owner_id):
        raise APIError(400, "not_a_member", "El usuario no es miembro de este grupo")
    await notify(
        user_id=new_owner_id,
        kind="group_ownership_received",
        actor=await _nombre_visible(username),
        group=group.get("name", ""),
    )
    return {"ok": True}

def _session_id(ga_token: Optional[str]) -> Optional[str]:
    """Sesión a la que pertenece la cookie, para reemitir su access.

    Cambiar de grupo no abre una sesión nueva: solo cambia el claim `gid`. Si
    aquí se emitiera una sesión aparte, la lista del perfil acumularía una fila
    por cada cambio de grupo y ninguna de ellas sería la que el usuario cerró.
    """
    if not ga_token:
        return None
    claims = decode_claims(ga_token)
    return claims.session_id if claims else None

@router.post("/switch/{group_id}")
async def switch_group(
    group_id: str,
    response: Response,
    username: str = Depends(require_auth),
    ga_token: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """Cambia el group activo del usuario y emite un nuevo token.

    Devuelve 403 si el group está desactivado o el usuario no es miembro.
    """
    _assert_not_guest(username)

    # Cambio al group personal propio: siempre permitido
    if group_id == username:
        reissue_access(response, username, _session_id(ga_token), group_id=username)
        return {"ok": True, "group_id": group_id}

    # Group de equipo: debe estar activo y el usuario debe ser miembro
    group = await _groups.get(group_id)
    if not group or group.get("status", "active") != "active":
        raise APIError(403, "group_unavailable", "Grupo no disponible o desactivado")
    if not await _groups.is_member(group_id, username):
        raise APIError(403, "not_a_member", "No eres miembro de este grupo")
    reissue_access(response, username, _session_id(ga_token), group_id=group_id)
    return {"ok": True, "group_id": group_id}
