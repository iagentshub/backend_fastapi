"""Rutas de skills."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api.routes.auth import GroupContext, require_group
from app.auth.auth import get_user_role
from app.config.data import DB_FILE, SKILLS_DIR
from app.errors import APIError
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.guest import get_session, is_guest
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.storage import SkillStorage
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.origin import compute_origin_type

router = APIRouter(prefix="/api/skills", tags=["skills"])

_storage = SkillStorage(SKILLS_DIR)
_shares = GroupShareStorage(DB_FILE)
_groups = GroupStorage(DB_FILE)
_versions = ResourceVersionStorage()

_VALID_SCOPES = {"public", "private", "all"}


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise APIError(400, "invalid_field", "Scope no válido", extra={"field": "scope"})


def _mark_origin(sk: Dict[str, Any], user: str, group_id: str) -> None:
    """Solo marca origin_type cuando es tuyo o enlazado — deja sin marcar las
    skills públicas de otros usuarios que aparecen en el listado (no son tuyas
    ni un enlace, no hay badge que mostrar)."""
    if sk.get("_shared") or sk.get("owner_id") in (user, group_id):
        sk["origin_type"] = compute_origin_type(sk)


@router.get("")
async def list_skills(
    scope: str = "all",
    owner_scope: str = "group",
    group_id: Optional[str] = None,
    include_inactive: bool = False,
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    user = ctx.user
    _check_scope(scope)
    if is_guest(user):
        s_obj = get_session(user)
        public = await _storage.list("public") if scope in ("public", "all") else []
        private = s_obj.skills if scope in ("private", "all") else []
        items = public + private
        for sk in items:
            _mark_origin(sk, user, ctx.group_id)
        if offset:
            items = items[offset:]
        if limit:
            items = items[:limit]
        return items
    items = await _storage.list(scope)
    role = await get_user_role(user)
    if group_id is not None:
        # Filtro por grupo: se aplica siempre, incluido admin
        if role != "admin" and not await _groups.can_access(group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        shared_ids = set(
            await _shares.get_group_shared_resource_ids(group_id, "skill")
        )
        items = [sk for sk in items if sk["id"] in shared_ids]
        for sk in items:
            sk["_shared"] = True
            sk["_group_id"] = group_id
    else:
        # Skills propias (personales o del group activo) + públicas + legacy sin owner
        # (incluye admin: la visibilidad global de admin se sirve vía /api/admin/*,
        # no filtrar aquí exponía skills privadas de otros usuarios sin marcar como ajenas)
        # + shares de todos los grupos del usuario.
        # En group de equipo (group_id != user), owner_id puede ser el UUID del group.
        group_id = ctx.group_id
        items = [
            sk
            for sk in items
            if sk.get("scope") == "public"
            or sk.get("owner_id") is None
            or sk.get("owner_id") == user
            or sk.get("owner_id") == group_id
        ]
        own_ids = {sk["id"] for sk in items}
        user_groups = await _groups.list_for_user(user)
        shared_map: Dict[str, str] = {}  # resource_id -> group_id
        for group in user_groups:
            gid = group["id"]
            for rid in await _shares.get_group_shared_resource_ids(gid, "skill"):
                if rid not in shared_map:
                    shared_map[rid] = gid
        for sid in set(shared_map.keys()) - own_ids:
            sk = await _storage.get_any(sid)
            if sk and await _groups.owner_is_active(sk.get("owner_id") or ""):
                sk["_shared"] = True
                sk["_group_id"] = shared_map[sid]
                items.append(sk)
    if not include_inactive:
        items = [sk for sk in items if sk.get("is_active", True)]
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    for sk in items:
        _mark_origin(sk, user, ctx.group_id)
    return items


@router.get("/{scope}/{skill_id}")
async def get_skill(
    scope: str, skill_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user = ctx.user
    _check_scope(scope)
    if is_guest(user) and scope == "private":
        sk = next(
            (s for s in get_session(user).skills if s.get("id") == skill_id), None
        )
        if not sk:
            raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
        _mark_origin(sk, user, ctx.group_id)
        return sk
    sk = await _storage.get(scope, skill_id)
    if not sk:
        raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})

    # Control de acceso: skills privadas solo para su propietario, admin o
    # miembros de un group al que la skill está compartida.
    if scope == "private" and not is_guest(user):
        user_group = ctx.group_id
        owner_id = sk.get("owner_id")
        if owner_id not in (user, user_group) and await get_user_role(user) != "admin":
            user_groups = await _groups.list_for_user(user)
            allowed = False
            if user_groups:
                group_ids = [g["id"] for g in user_groups]
                for gid in group_ids:
                    shared = await _shares.get_group_shared_resource_ids(
                        gid, "skill"
                    )
                    if skill_id in shared:
                        allowed = True
                        break
            if not allowed:
                raise APIError(403, "forbidden", "No tienes acceso a esta skill")
            sk["_shared"] = True

    _mark_origin(sk, user, ctx.group_id)
    return sk


@router.post("/{scope}")
async def save_skill(
    scope: str, request: Request, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    payload = await request.json()
    if is_guest(user):
        if scope == "public":
            raise APIError(
                403,
                "guest_public_skill_edit_forbidden",
                "Los invitados no pueden modificar skills públicas",
            )
        s = get_session(user)
        guest_id = payload.get("id")
        if guest_id and not any(sk.get("id") == guest_id for sk in s.skills):
            guest_id = None
        skill: Dict[str, Any] = {
            **payload,
            "id": guest_id or generate_id(),
            "scope": "private",
        }
        s.skills = [sk for sk in s.skills if sk.get("id") != skill["id"]]
        s.skills.append(skill)
        return skill
    skill_id_in_payload = payload.get("id")
    existing = (
        await _storage.get_any(skill_id_in_payload) if skill_id_in_payload else None
    )
    if skill_id_in_payload and not existing:
        # Un id entrante solo es válido para editar una fila existente;
        # en altas el id lo genera siempre el servidor.
        payload.pop("id", None)
    was_update = existing is not None
    try:
        saved = await _storage.save(scope, payload, owner_id=group_id)
        await _versions.create(
            "skill", saved["id"], group_id, saved, user, reason="save"
        )
        action = "actualizada" if was_update else "creada"
        flog.info(
            f"Skill {action}: {saved['id']} {saved.get('name', '')!r}", username=user
        )
        return saved
    except ValueError as e:
        raise APIError(422, "invalid_skill_data", str(e))


@router.delete("/{scope}/{skill_id}")
async def delete_skill(
    scope: str, skill_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if is_guest(user):
        if scope == "public":
            raise APIError(
                403,
                "guest_public_skill_delete_forbidden",
                "Los invitados no pueden eliminar skills públicas",
            )
        s = get_session(user)
        before = len(s.skills)
        s.skills = [sk for sk in s.skills if sk.get("id") != skill_id]
        if len(s.skills) == before:
            raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
        return {"ok": True}
    # Ownership check before delete
    sk = await _storage.get_any(skill_id)
    role = await get_user_role(user)
    if sk and role != "admin" and sk.get("owner_id") not in (group_id, None):
        raise APIError(403, "forbidden", "No tienes permiso para eliminar esta skill")
    try:
        delete_owner = None if role == "admin" else group_id
        if not await _storage.delete(scope, skill_id, owner_id=delete_owner):
            raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
    except ValueError as e:
        raise APIError(403, "public_skill_readonly", str(e))
    flog.info(f"Skill borrada: {skill_id} {(sk or {}).get('name', '')!r}", username=user)
    return {"ok": True}


async def _set_skill_active(
    skill_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        raise APIError(403, "forbidden", "Los invitados no pueden desactivar skills")
    sk = await _storage.get_any(skill_id)
    if not sk:
        raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
    role = await get_user_role(user)
    if role != "admin" and sk.get("owner_id") not in (group_id, None):
        raise APIError(403, "forbidden", "Solo el propietario puede cambiar el estado")
    owner = None if role == "admin" else group_id
    if not await _storage.set_active(skill_id, owner, active):
        raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
    estado = "activada" if active else "desactivada"
    flog.info(f"Skill {estado}: {skill_id} {sk.get('name', '')!r}", username=user)
    return {"ok": True, "is_active": active}


@router.post("/{skill_id}/activate")
async def activate_skill(
    skill_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_skill_active(skill_id, True, ctx)


@router.post("/{skill_id}/deactivate")
async def deactivate_skill(
    skill_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_skill_active(skill_id, False, ctx)
