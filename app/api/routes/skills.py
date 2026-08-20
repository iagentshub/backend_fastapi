"""Rutas de skills."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Response

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.config.data import SKILLS_DIR
from app.errors import APIError
from app.models.request_bodies import CatalogResourcePayload
from app.pagination.models import OffsetParams
from app.services.publishing import assert_can_publish
from app.services.scoped_resource_listing import list_authenticated_scoped_resources
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.skill_storage import (
    SKILL_ASSIGNABLE_LABELS,
    SKILL_CATEGORIES,
    SKILL_LABELS,
    SkillStorage,
    ensure_origin_label,
)
from app.utils import flog
from app.utils.origin import assert_resource_writable, compute_origin_type

router = APIRouter(prefix="/api/skills", tags=["skills"])

_storage = SkillStorage(SKILLS_DIR)
_shares = GroupShareStorage()
_groups = GroupStorage()
_versions = ResourceVersionStorage()

_VALID_SCOPES = {"public", "private", "all"}


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise APIError(
            400, "invalid_field", "Scope no válido", extra={"field": "scope"}
        )


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
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    _check_scope(scope)
    return await list_authenticated_scoped_resources(
        _storage,
        ctx=ctx,
        scope=scope,
        page=OffsetParams(limit=limit, offset=offset),
        response=response,
        requested_group_id=group_id,
        mark_origin=_mark_origin,
    )


@router.get("/{scope}/{skill_id}")
async def get_skill(
    scope: str, skill_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user = ctx.user
    _check_scope(scope)
    sk = await _storage.get(scope, skill_id)
    if not sk:
        raise APIError(
            404, "not_found", "Skill no encontrada", extra={"resource": "skill"}
        )

    # Control de acceso: skills privadas solo para su propietario, admin o
    # miembros de un group al que la skill está compartida.
    if scope == "private":
        user_group = ctx.group_id
        owner_id = sk.get("owner_id")
        if owner_id not in (user, user_group) and await get_user_role(user) != "admin":
            user_groups = await _groups.list_for_user(user)
            allowed = False
            if user_groups:
                group_ids = [g["id"] for g in user_groups]
                for gid in group_ids:
                    shared = await _shares.get_group_shared_resource_ids(gid, "skill")
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
    scope: str,
    body: CatalogResourcePayload,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if scope == "public":
        assert_can_publish(user)
    payload = body.payload()
    if payload.get("tags") not in (None, [], ""):
        raise APIError(
            422,
            "invalid_field",
            "Las skills no admiten tags libres",
            extra={"field": "tags"},
        )
    payload.pop("tags", None)
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
                "La skill contiene labels que no existen en el catálogo del sistema",
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
                "La skill contiene labels mutuamente excluyentes",
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
    category = str(payload.get("category") or "").strip()
    if category and category not in SKILL_CATEGORIES:
        raise APIError(
            422,
            "invalid_field",
            "Categoría de skill no válida",
            extra={"field": "category"},
        )
    skill_id_in_payload = payload.get("id")
    existing = None
    if skill_id_in_payload:
        existing = await _storage.get_any(skill_id_in_payload, owner_id=group_id)
        if existing:
            assert_resource_writable(existing, "skill")
        if not existing and await _storage.get_any(skill_id_in_payload):
            raise APIError(
                403,
                "forbidden",
                "No tienes permiso para editar esta skill",
                extra={"resource": "skill"},
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
    scope: str, skill_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if scope == "public":
        assert_can_publish(user)
    # Ownership check before delete
    sk = await _storage.get_any(skill_id)
    if sk:
        assert_resource_writable(sk, "skill")
    role = await get_user_role(user)
    if sk and sk.get("scope") == "public" and sk.get("owner_id") is None:
        raise APIError(
            403,
            "public_skill_readonly",
            "Las skills públicas de sistema son de solo lectura",
        )
    if sk and role != "admin" and sk.get("owner_id") != group_id:
        raise APIError(403, "forbidden", "No tienes permiso para eliminar esta skill")
    try:
        delete_owner = (
            sk.get("owner_id")
            if scope == "public" and sk
            else (None if role == "admin" else group_id)
        )
        if not await _storage.delete(scope, skill_id, owner_id=delete_owner):
            raise APIError(
                404, "not_found", "Skill no encontrada", extra={"resource": "skill"}
            )
    except ValueError as e:
        raise APIError(403, "public_skill_readonly", str(e))
    flog.info(
        f"Skill borrada: {skill_id} {(sk or {}).get('name', '')!r}", username=user
    )
    return {"ok": True}
