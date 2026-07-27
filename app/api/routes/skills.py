"""Rutas de skills."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request

from app.api.routes.auth import WorkspaceContext, require_workspace
from app.auth.auth import get_user_role
from app.config.data import DB_FILE, SKILLS_DIR
from app.errors import APIError

from app.storage.guest import get_session, is_guest
from app.storage.folders import FolderStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.storage import SkillStorage
from app.storage.workspace_shares import WorkspaceShareStorage
from app.storage.workspaces import WorkspaceStorage
from app.utils.origin import compute_origin_type

router = APIRouter(prefix="/api/skills", tags=["skills"])

_storage = SkillStorage(SKILLS_DIR)
_shares = WorkspaceShareStorage(DB_FILE)
_ws = WorkspaceStorage(DB_FILE)
_folders = FolderStorage()
_versions = ResourceVersionStorage()

_VALID_SCOPES = {"public", "private", "all"}


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise APIError(400, "invalid_field", "Scope no válido", extra={"field": "scope"})


def _mark_origin(sk: Dict[str, Any], user: str, workspace_id: str) -> None:
    """Solo marca origin_type cuando es tuyo o enlazado — deja sin marcar las
    skills públicas de otros usuarios que aparecen en el listado (no son tuyas
    ni un enlace, no hay badge que mostrar)."""
    if sk.get("_shared") or sk.get("owner_id") in (user, workspace_id):
        sk["origin_type"] = compute_origin_type(sk)


@router.get("")
async def list_skills(
    scope: str = "all",
    owner_scope: str = "workspace",
    group_id: Optional[str] = None,
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    user = ctx.user
    _check_scope(scope)
    if is_guest(user):
        s_obj = get_session(user)
        public = await _storage.list("public") if scope in ("public", "all") else []
        private = s_obj.skills if scope in ("private", "all") else []
        items = public + private
        for sk in items:
            _mark_origin(sk, user, ctx.workspace_id)
        if offset:
            items = items[offset:]
        if limit:
            items = items[:limit]
        return items
    items = await _storage.list(scope)
    role = await get_user_role(user)
    if group_id is not None:
        # Filtro por grupo: se aplica siempre, incluido admin
        if role != "admin" and not await _ws.can_access(group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        shared_ids = set(
            await _shares.get_workspace_shared_resource_ids(group_id, "skill")
        )
        items = [sk for sk in items if sk["id"] in shared_ids]
        for sk in items:
            sk["_shared"] = True
            sk["_group_id"] = group_id
    elif role != "admin":
        # Skills propias (personales o del workspace activo) + públicas + legacy sin owner
        # + shares de todos los grupos del usuario.
        # En workspace de equipo (workspace_id != user), owner_id puede ser el UUID del workspace.
        workspace_id = ctx.workspace_id
        items = [
            sk
            for sk in items
            if sk.get("scope") == "public"
            or sk.get("owner_id") is None
            or sk.get("owner_id") == user
            or sk.get("owner_id") == workspace_id
        ]
        own_ids = {sk["id"] for sk in items}
        user_groups = await _ws.list_for_user(user)
        shared_map: Dict[str, str] = {}  # resource_id -> group_id
        for group in user_groups:
            gid = group["id"]
            for rid in await _shares.get_workspace_shared_resource_ids(gid, "skill"):
                if rid not in shared_map:
                    shared_map[rid] = gid
        for sid in set(shared_map.keys()) - own_ids:
            sk = await _storage.get_any(sid)
            if sk and await _ws.owner_is_active(sk.get("owner_id") or ""):
                sk["_shared"] = True
                sk["_group_id"] = shared_map[sid]
                items.append(sk)
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    for sk in items:
        _mark_origin(sk, user, ctx.workspace_id)
    private_items = [item for item in items if item.get("scope") != "public"]
    enriched = await _folders.enrich_items(
        private_items, default_owner=ctx.workspace_id, resource_type="skill"
    )
    folders = {item["id"]: item.get("folder_id") for item in enriched}
    return [
        {**item, "folder_id": folders.get(item["id"])}
        if item.get("scope") != "public"
        else item
        for item in items
    ]


@router.get("/{scope}/{skill_id}")
async def get_skill(
    scope: str, skill_id: str, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user = ctx.user
    _check_scope(scope)
    if is_guest(user) and scope == "private":
        sk = next(
            (s for s in get_session(user).skills if s.get("id") == skill_id), None
        )
        if not sk:
            raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
        _mark_origin(sk, user, ctx.workspace_id)
        return sk
    sk = await _storage.get(scope, skill_id)
    if not sk:
        raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})

    # Control de acceso: skills privadas solo para su propietario, admin o
    # miembros de un workspace al que la skill está compartida.
    if scope == "private" and not is_guest(user):
        user_ws = ctx.workspace_id
        owner_id = sk.get("owner_id")
        if owner_id not in (user, user_ws) and await get_user_role(user) != "admin":
            user_groups = await _ws.list_for_user(user)
            allowed = False
            if user_groups:
                group_ids = [g["id"] for g in user_groups]
                for gid in group_ids:
                    shared = await _shares.get_workspace_shared_resource_ids(
                        gid, "skill"
                    )
                    if skill_id in shared:
                        allowed = True
                        break
            if not allowed:
                raise APIError(403, "forbidden", "No tienes acceso a esta skill")
            sk["_shared"] = True

    _mark_origin(sk, user, ctx.workspace_id)
    return sk


@router.post("/{scope}")
async def save_skill(
    scope: str, request: Request, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
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
        skill: Dict[str, Any] = {
            **payload,
            "id": payload.get("id") or uuid4().hex[:12],
            "scope": "private",
        }
        s.skills = [sk for sk in s.skills if sk.get("id") != skill["id"]]
        s.skills.append(skill)
        return skill
    try:
        folder_id = payload.pop("folder_id", None)
        saved = await _storage.save(scope, payload, owner_id=workspace_id)
        if scope == "private" and folder_id is not None:
            try:
                await _folders.assign(workspace_id, "skill", saved["id"], folder_id or None)
            except ValueError as exc:
                raise APIError(422, "incompatible_folder", str(exc)) from exc
        saved["folder_id"] = await _folders.folder_for(workspace_id, "skill", saved["id"])
        await _versions.create(
            "skill", saved["id"], workspace_id, saved, user, reason="save"
        )
        return saved
    except ValueError as e:
        raise APIError(422, "invalid_skill_data", str(e))


@router.delete("/{scope}/{skill_id}")
async def delete_skill(
    scope: str, skill_id: str, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
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
    if (
        sk
        and await get_user_role(user) != "admin"
        and sk.get("owner_id") not in (workspace_id, None)
    ):
        raise APIError(403, "forbidden", "No tienes permiso para eliminar esta skill")
    try:
        if not await _storage.delete(scope, skill_id):
            raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
    except ValueError as e:
        raise APIError(403, "public_skill_readonly", str(e))
    if sk:
        await _folders.remove_resource(
            str(sk.get("owner_id") or workspace_id), "skill", skill_id
        )
    return {"ok": True}


@router.patch("/private/{skill_id}/folder")
async def move_skill_to_folder(
    skill_id: str, request: Request, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    skill = await _storage.get("private", skill_id)
    if not skill:
        raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
    if await get_user_role(ctx.user) != "admin" and skill.get("owner_id") != ctx.workspace_id:
        raise APIError(403, "forbidden", "Solo el propietario puede mover la skill")
    body = await request.json()
    try:
        await _folders.assign(
            ctx.workspace_id, "skill", skill_id,
            str(body["folder_id"]) if body.get("folder_id") else None,
        )
    except ValueError as exc:
        raise APIError(422, "incompatible_folder", str(exc)) from exc
    return {**skill, "folder_id": body.get("folder_id")}
