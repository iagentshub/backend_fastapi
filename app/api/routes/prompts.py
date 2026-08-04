"""Rutas de prompts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api.routes.auth import GroupContext, require_group
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.errors import APIError
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.guest import get_session, is_guest
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.storage import (
    PROMPT_ALIAS_RE,
    SKILL_ASSIGNABLE_LABELS,
    PromptStorage,
)
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.net import json_body
from app.utils.origin import compute_origin_type

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

_storage = PromptStorage()
_shares = GroupShareStorage(DB_FILE)
_groups = GroupStorage(DB_FILE)
_versions = ResourceVersionStorage()

_VALID_SCOPES = {"public", "private", "all"}


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise APIError(
            400, "invalid_field", "Scope no válido", extra={"field": "scope"}
        )


def _mark_origin(pr: Dict[str, Any], user: str, group_id: str) -> None:
    """Solo marca origin_type cuando es tuyo o enlazado — deja sin marcar los
    prompts públicos de otros usuarios que aparecen en el listado (no son tuyos
    ni un enlace, no hay badge que mostrar)."""
    if pr.get("_shared") or pr.get("owner_id") in (user, group_id):
        pr["origin_type"] = compute_origin_type(pr)


@router.get("")
async def list_prompts(
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
        private = s_obj.prompts if scope in ("private", "all") else []
        items = public + private
        if not include_inactive:
            items = [pr for pr in items if pr.get("is_active", True)]
        for pr in items:
            _mark_origin(pr, user, ctx.group_id)
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
            await _shares.get_group_shared_resource_ids(group_id, "prompt")
        )
        items = [pr for pr in items if pr["id"] in shared_ids]
        for pr in items:
            pr["_shared"] = True
            pr["_group_id"] = group_id
    else:
        # Prompts propios (personales o del group activo) + públicos + legacy sin owner
        # + shares de todos los grupos del usuario.
        group_id = ctx.group_id
        items = [
            pr
            for pr in items
            if pr.get("scope") == "public"
            or pr.get("owner_id") is None
            or pr.get("owner_id") == user
            or pr.get("owner_id") == group_id
        ]
        own_ids = {pr["id"] for pr in items}
        user_groups = await _groups.list_for_user(user)
        shared_map: Dict[str, str] = {}  # resource_id -> group_id
        for group in user_groups:
            gid = group["id"]
            for rid in await _shares.get_group_shared_resource_ids(gid, "prompt"):
                if rid not in shared_map:
                    shared_map[rid] = gid
        for pid in set(shared_map.keys()) - own_ids:
            pr = await _storage.get_any(pid)
            if pr and await _groups.owner_is_active(pr.get("owner_id") or ""):
                pr["_shared"] = True
                pr["_group_id"] = shared_map[pid]
                items.append(pr)
    if not include_inactive:
        items = [pr for pr in items if pr.get("is_active", True)]
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    for pr in items:
        _mark_origin(pr, user, ctx.group_id)
    return items


@router.get("/{scope}/{prompt_id}")
async def get_prompt(
    scope: str, prompt_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user = ctx.user
    _check_scope(scope)
    if is_guest(user) and scope == "private":
        pr = next(
            (p for p in get_session(user).prompts if p.get("id") == prompt_id), None
        )
        if not pr:
            raise APIError(
                404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
            )
        _mark_origin(pr, user, ctx.group_id)
        return pr
    pr = await _storage.get(scope, prompt_id)
    if not pr:
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )

    # Control de acceso: prompts privados solo para su propietario, admin o
    # miembros de un group al que el prompt está compartido.
    if scope == "private" and not is_guest(user):
        user_group = ctx.group_id
        owner_id = pr.get("owner_id")
        if owner_id not in (user, user_group) and await get_user_role(user) != "admin":
            user_groups = await _groups.list_for_user(user)
            allowed = False
            if user_groups:
                group_ids = [g["id"] for g in user_groups]
                for gid in group_ids:
                    shared = await _shares.get_group_shared_resource_ids(gid, "prompt")
                    if prompt_id in shared:
                        allowed = True
                        break
            if not allowed:
                raise APIError(403, "forbidden", "No tienes acceso a este prompt")
            pr["_shared"] = True

    _mark_origin(pr, user, ctx.group_id)
    return pr


@router.post("/{scope}")
async def save_prompt(
    scope: str, request: Request, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    payload = await json_body(request)
    raw_labels = payload.get("labels")
    if raw_labels is not None:
        if not isinstance(raw_labels, list):
            raise APIError(
                422,
                "invalid_field",
                "Las labels deben ser una lista del catálogo del sistema",
                extra={"field": "labels"},
            )
        labels = list(
            dict.fromkeys(
                str(label).strip() for label in raw_labels if str(label).strip()
            )
        )
        invalid_labels = [
            label for label in labels if label not in SKILL_ASSIGNABLE_LABELS
        ]
        if invalid_labels:
            raise APIError(
                422,
                "invalid_field",
                "El prompt contiene labels que no existen en el catálogo del sistema",
                extra={"field": "labels", "invalid": invalid_labels},
            )
        visibility = [label for label in labels if label in {"private", "public"}]
        environments = [
            label
            for label in labels
            if label in {"production", "staging", "development", "test"}
        ]
        if len(visibility) > 1 or len(environments) > 1:
            raise APIError(
                422,
                "invalid_field",
                "El prompt contiene labels mutuamente excluyentes",
                extra={"field": "labels"},
            )
        if not visibility:
            labels.insert(0, scope if scope in {"private", "public"} else "private")
        payload["labels"] = labels
    alias = str(payload.get("alias") or "").strip().lower()
    if not PROMPT_ALIAS_RE.match(alias):
        raise APIError(
            422,
            "invalid_field",
            "Alias inválido: usa minúsculas, números, guion o guion bajo, 3-30 caracteres",
            extra={"field": "alias"},
        )
    payload["alias"] = alias
    if is_guest(user):
        if scope == "public":
            raise APIError(
                403,
                "guest_public_prompt_edit_forbidden",
                "Los invitados no pueden modificar prompts públicos",
            )
        s = get_session(user)
        guest_id = payload.get("id")
        if guest_id and not any(pr.get("id") == guest_id for pr in s.prompts):
            guest_id = None
        prompt: Dict[str, Any] = {
            **payload,
            "id": guest_id or generate_id(),
            "resource_type": "prompt",
            "scope": "private",
            "is_active": True,
        }
        s.prompts = [pr for pr in s.prompts if pr.get("id") != prompt["id"]]
        s.prompts.append(prompt)
        return prompt
    prompt_id_in_payload = payload.get("id")
    existing = None
    if prompt_id_in_payload:
        existing = await _storage.get_any(prompt_id_in_payload, owner_id=group_id)
        if not existing and await _storage.get_any(prompt_id_in_payload):
            raise APIError(
                403,
                "forbidden",
                "No tienes permiso para editar este prompt",
                extra={"resource": "prompt"},
            )
    if prompt_id_in_payload and not existing:
        # Un id entrante solo es válido para editar una fila existente;
        # en altas el id lo genera siempre el servidor.
        payload.pop("id", None)
    was_update = existing is not None
    try:
        saved = await _storage.save(scope, payload, owner_id=group_id)
        await _versions.create(
            "prompt", saved["id"], group_id, saved, user, reason="save"
        )
        action = "actualizado" if was_update else "creado"
        flog.info(
            f"Prompt {action}: {saved['id']} {saved.get('name', '')!r}", username=user
        )
        return saved
    except ValueError as e:
        msg = str(e)
        if msg == "Ya tienes un prompt con ese alias":
            raise APIError(
                409, "already_exists", msg, extra={"resource": "prompt"}
            ) from e
        raise APIError(422, "invalid_prompt_data", msg) from e


@router.delete("/{scope}/{prompt_id}")
async def delete_prompt(
    scope: str, prompt_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if is_guest(user):
        if scope == "public":
            raise APIError(
                403,
                "guest_public_prompt_delete_forbidden",
                "Los invitados no pueden eliminar prompts públicos",
            )
        s = get_session(user)
        before = len(s.prompts)
        s.prompts = [pr for pr in s.prompts if pr.get("id") != prompt_id]
        if len(s.prompts) == before:
            raise APIError(
                404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
            )
        return {"ok": True}
    # Ownership check before delete
    pr = await _storage.get_any(prompt_id)
    role = await get_user_role(user)
    if pr and pr.get("scope") == "public" and pr.get("owner_id") is None:
        raise APIError(
            403,
            "public_prompt_readonly",
            "Los prompts públicos de sistema son de solo lectura",
        )
    if pr and role != "admin" and pr.get("owner_id") != group_id:
        raise APIError(
            403, "forbidden", "No tienes permiso para eliminar este prompt"
        )
    try:
        delete_owner = (
            pr.get("owner_id")
            if scope == "public" and pr
            else (None if role == "admin" else group_id)
        )
        if not await _storage.delete(scope, prompt_id, owner_id=delete_owner):
            raise APIError(
                404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
            )
    except ValueError as e:
        raise APIError(403, "public_prompt_readonly", str(e)) from e
    flog.info(
        f"Prompt borrado: {prompt_id} {(pr or {}).get('name', '')!r}", username=user
    )
    return {"ok": True}


async def _set_prompt_active(
    prompt_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        raise APIError(403, "forbidden", "Los invitados no pueden desactivar prompts")
    pr = await _storage.get_any(prompt_id)
    if not pr:
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )
    role = await get_user_role(user)
    if role != "admin" and pr.get("owner_id") not in (group_id, None):
        raise APIError(403, "forbidden", "Solo el propietario puede cambiar el estado")
    owner = None if role == "admin" else group_id
    if not await _storage.set_active(prompt_id, owner, active):
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )
    estado = "activado" if active else "desactivado"
    flog.info(f"Prompt {estado}: {prompt_id} {pr.get('name', '')!r}", username=user)
    return {"ok": True, "is_active": active}


@router.post("/{prompt_id}/activate")
async def activate_prompt(
    prompt_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_prompt_active(prompt_id, True, ctx)


@router.post("/{prompt_id}/deactivate")
async def deactivate_prompt(
    prompt_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_prompt_active(prompt_id, False, ctx)
