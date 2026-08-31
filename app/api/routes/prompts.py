"""Rutas de prompts."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.errors import APIError
from app.models.request_bodies import CatalogResourcePayload
from app.services.publishing import assert_can_publish
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.prompt_storage import PROMPT_ALIAS_RE, PromptStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.skill_storage import (
    SKILL_ASSIGNABLE_LABELS,
    SKILL_LABELS,
    ensure_origin_label,
)
from app.utils import flog
from app.utils.origin import assert_resource_writable, compute_origin_type

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

_storage = PromptStorage()
_shares = GroupShareStorage()
_groups = GroupStorage()
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


@router.get("/{scope}/{prompt_id}")
async def get_prompt(
    scope: str, prompt_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user = ctx.user
    _check_scope(scope)
    pr = await _storage.get(scope, prompt_id)
    if not pr:
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )

    # Control de acceso: prompts privados solo para su propietario, admin o
    # miembros de un group al que el prompt está compartido.
    if scope == "private":
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
    scope: str,
    body: CatalogResourcePayload,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if scope == "public":
        assert_can_publish(user)
    payload = body.payload()
    role = await get_user_role(user)
    allowed_labels = (
        SKILL_LABELS
        if role == "admin"
        else SKILL_ASSIGNABLE_LABELS | {"community", "fork"}
    )
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
        invalid_labels = [label for label in labels if label not in allowed_labels]
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
    if role != "admin":
        payload["labels"] = ensure_origin_label(
            [str(label) for label in (payload.get("labels") or [scope]) if label],
            "community",
        )
    alias = str(payload.get("alias") or "").strip().lower()
    if not PROMPT_ALIAS_RE.match(alias):
        raise APIError(
            422,
            "invalid_field",
            "Alias inválido: usa minúsculas, números, guion o guion bajo, 3-30 caracteres",
            extra={"field": "alias"},
        )
    payload["alias"] = alias
    prompt_id_in_payload = payload.get("id")
    existing = None
    if prompt_id_in_payload:
        existing = await _storage.get_any(prompt_id_in_payload, owner_id=group_id)
        if existing:
            assert_resource_writable(existing, "prompt")
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


async def _set_prompt_active(
    scope: str, prompt_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    _check_scope(scope)
    prompt = await _storage.get(scope, prompt_id)
    if prompt is None:
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )
    assert_resource_writable(prompt, "prompt")
    role = await get_user_role(ctx.user)
    if role != "admin" and prompt.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "No tienes permiso para modificar este prompt")
    owner = None if role == "admin" else str(prompt["owner_id"])
    if not await _storage.set_active(prompt_id, owner, active):
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )
    return await _storage.get(scope, prompt_id) or {
        "id": prompt_id,
        "is_active": active,
    }


@router.post("/{scope}/{prompt_id}/activate")
async def activate_prompt(
    scope: str, prompt_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_prompt_active(scope, prompt_id, True, ctx)


@router.post("/{scope}/{prompt_id}/deactivate")
async def deactivate_prompt(
    scope: str, prompt_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_prompt_active(scope, prompt_id, False, ctx)


@router.delete("/{scope}/{prompt_id}")
async def delete_prompt(
    scope: str, prompt_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if scope == "public":
        assert_can_publish(user)
    # Ownership check before delete
    pr = await _storage.get_any(prompt_id)
    if pr:
        assert_resource_writable(pr, "prompt")
    role = await get_user_role(user)
    if pr and pr.get("scope") == "public" and pr.get("owner_id") is None:
        raise APIError(
            403,
            "public_prompt_readonly",
            "Los prompts públicos de sistema son de solo lectura",
        )
    if pr and role != "admin" and pr.get("owner_id") != group_id:
        raise APIError(403, "forbidden", "No tienes permiso para eliminar este prompt")
    try:
        delete_owner = (
            pr.get("owner_id")
            if scope == "public" and pr
            else (None if role == "admin" else group_id)
        )
        deleted = (
            await _storage.delete_as_admin(scope, prompt_id)
            if delete_owner is None
            else await _storage.delete(scope, prompt_id, owner_id=delete_owner)
        )
        if not deleted:
            raise APIError(
                404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
            )
    except ValueError as e:
        raise APIError(403, "public_prompt_readonly", str(e)) from e
    flog.info(
        f"Prompt borrado: {prompt_id} {(pr or {}).get('name', '')!r}", username=user
    )
    return {"ok": True}
