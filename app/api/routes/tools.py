"""Rutas de tools (herramientas ejecutables).

Fase 1: solo catalogación y asignación a agentes — sin motor de ejecución.
Calcado de skills.py, con dos diferencias estructurales: `language`
(obligatorio) sustituye a `category`, y hay contenido dual texto/binario
(python/shell usan `content`; cpp usa un binario subido aparte).

Estas rutas estuvieron cerradas a los invitados mientras su sesión era un dict
en memoria: GuestSession no contemplaba tools y escribir la rama habría sido
duplicar cada handler. Hoy el invitado es un usuario efímero y las tools son
parte de su espacio personal como el resto. Lo único que sigue cerrado es
publicar (`assert_can_publish`), igual que en skills y prompts.
Ver docs/adr/012-el-invitado-es-un-usuario-efimero.md.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.errors import APIError
from app.models.request_bodies import CatalogResourcePayload
from app.pagination.models import OffsetParams
from app.services.publishing import assert_can_publish
from app.services.scoped_resource_listing import list_authenticated_scoped_resources
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.skill_storage import (
    SKILL_ASSIGNABLE_LABELS,
    SKILL_LABELS,
    ensure_origin_label,
)
from app.storage.tool_storage import TOOL_LANGUAGES, ToolStorage
from app.utils import flog
from app.utils.origin import assert_resource_writable, compute_origin_type

router = APIRouter(prefix="/api/tools", tags=["tools"])

_storage = ToolStorage()
_shares = GroupShareStorage()
_groups = GroupStorage()

_VALID_SCOPES = {"public", "private", "all"}

# 50 MB — igual límite que documenta el plan de la Fase 1.
_MAX_TOOL_BINARY_BYTES = 50 * 1024 * 1024


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise APIError(
            400, "invalid_field", "Scope no válido", extra={"field": "scope"}
        )


def _mark_origin(tl: Dict[str, Any], user: str, group_id: str) -> None:
    """Solo marca origin_type cuando es tuyo o enlazado — deja sin marcar las
    tools públicas de otros usuarios que aparecen en el listado (no son tuyas
    ni un enlace, no hay badge que mostrar)."""
    if tl.get("_shared") or tl.get("owner_id") in (user, group_id):
        tl["origin_type"] = compute_origin_type(tl)


async def _assert_read_access(
    tool_id: str, tl: Dict[str, Any], ctx: GroupContext
) -> None:
    """Lanza 403 si el usuario no puede leer una tool privada (mismo patrón
    que get_skill/get_prompt): propietario, group activo, admin, o compartida
    con alguno de los grupos del usuario."""
    user_group = ctx.group_id
    owner_id = tl.get("owner_id")
    if owner_id in (ctx.user, user_group):
        return
    if await get_user_role(ctx.user) == "admin":
        return
    user_groups = await _groups.list_for_user(ctx.user)
    if user_groups:
        group_ids = [g["id"] for g in user_groups]
        for gid in group_ids:
            shared = await _shares.get_group_shared_resource_ids(gid, "tool")
            if tool_id in shared:
                tl["_shared"] = True
                return
    raise APIError(403, "forbidden", "No tienes acceso a esta tool")


@router.get("")
async def list_tools(
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


@router.get("/{scope}/{tool_id}")
async def get_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user = ctx.user
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )

    if scope == "private":
        await _assert_read_access(tool_id, tl, ctx)

    _mark_origin(tl, user, ctx.group_id)
    tl.pop("binary_b64", None)
    return tl


@router.post("/{scope}")
async def save_tool(
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
                "La tool contiene labels que no existen en el catálogo del sistema",
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
                "La tool contiene labels mutuamente excluyentes",
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

    language = str(payload.get("language") or "").strip()
    if language not in TOOL_LANGUAGES:
        raise APIError(
            422,
            "invalid_field",
            "Lenguaje de tool no válido",
            extra={"field": "language"},
        )
    payload["language"] = language
    # El contenido de una tool cpp vive solo en el binario subido aparte.
    if language == "cpp":
        payload["content"] = ""

    tool_id_in_payload = payload.get("id")
    existing = None
    if tool_id_in_payload:
        existing = await _storage.get_any(tool_id_in_payload, owner_id=group_id)
        if existing:
            assert_resource_writable(existing, "tool")
        if not existing and await _storage.get_any(tool_id_in_payload):
            raise APIError(
                403,
                "forbidden",
                "No tienes permiso para editar esta tool",
                extra={"resource": "tool"},
            )
    if tool_id_in_payload and not existing:
        # Un id entrante solo es válido para editar una fila existente;
        # en altas el id lo genera siempre el servidor.
        payload.pop("id", None)
    was_update = existing is not None
    try:
        saved = await _storage.save(scope, payload, owner_id=group_id)
        action = "actualizada" if was_update else "creada"
        flog.info(
            f"Tool {action}: {saved['id']} {saved.get('name', '')!r}", username=user
        )
        saved.pop("binary_b64", None)
        return saved
    except ValueError as e:
        raise APIError(422, "invalid_tool_data", str(e)) from e


@router.delete("/{scope}/{tool_id}")
async def delete_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if scope == "public":
        assert_can_publish(user)
    # Ownership check before delete
    tl = await _storage.get_any(tool_id)
    if tl:
        assert_resource_writable(tl, "tool")
    role = await get_user_role(user)
    if tl and tl.get("scope") == "public" and tl.get("owner_id") is None:
        raise APIError(
            403,
            "public_tool_readonly",
            "Las tools públicas de sistema son de solo lectura",
        )
    if tl and role != "admin" and tl.get("owner_id") != group_id:
        raise APIError(403, "forbidden", "No tienes permiso para eliminar esta tool")
    try:
        delete_owner = (
            tl.get("owner_id")
            if scope == "public" and tl
            else (None if role == "admin" else group_id)
        )
        if not await _storage.delete(scope, tool_id, owner_id=delete_owner):
            raise APIError(
                404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
            )
    except ValueError as e:
        raise APIError(403, "public_tool_readonly", str(e)) from e
    flog.info(f"Tool borrada: {tool_id} {(tl or {}).get('name', '')!r}", username=user)
    return {"ok": True}


async def _set_tool_active(
    scope: str, tool_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    _check_scope(scope)
    tool = await _storage.get(scope, tool_id)
    if tool is None:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    assert_resource_writable(tool, "tool")
    role = await get_user_role(ctx.user)
    if role != "admin" and tool.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "No tienes permiso para modificar esta tool")
    owner = None if role == "admin" else str(tool["owner_id"])
    if not await _storage.set_active(tool_id, owner, active):
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    return await _storage.get(scope, tool_id) or {"id": tool_id, "is_active": active}


@router.post("/{scope}/{tool_id}/activate")
async def activate_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_tool_active(scope, tool_id, True, ctx)


@router.post("/{scope}/{tool_id}/deactivate")
async def deactivate_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_tool_active(scope, tool_id, False, ctx)


# ── Binario (solo tools cpp) — subida/descarga en dos pasos ─────────────────
# Mismo patrón que auth.py::upload_avatar / users.py::get_avatar: JSON de
# metadatos primero (POST /api/tools/{scope}), binario aparte.


@router.post("/{scope}/{tool_id}/binary")
async def upload_tool_binary(
    scope: str,
    tool_id: str,
    file: UploadFile = File(...),
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    assert_resource_writable(tl, "tool")
    role = await get_user_role(user)
    owner_id = tl.get("owner_id")
    if role != "admin" and owner_id not in (user, group_id):
        raise APIError(403, "forbidden", "No tienes permiso para modificar esta tool")
    if tl.get("language") != "cpp":
        raise APIError(
            422,
            "tool_language_not_binary",
            "Solo las tools de lenguaje 'cpp' admiten binario",
            extra={"field": "language"},
        )

    data = await file.read()
    if not data:
        raise APIError(400, "tool_binary_empty", "El binario no puede estar vacío")
    if len(data) > _MAX_TOOL_BINARY_BYTES:
        raise APIError(
            400, "tool_binary_too_large", "El binario no puede superar 50 MB."
        )

    # Sin allowlist de extensión (a diferencia del avatar): un binario ELF en
    # Linux normalmente no tiene extensión. Solo se sanea el nombre.
    filename = Path(file.filename or "tool_binary").name[:255] or "tool_binary"
    encoded = base64.b64encode(data).decode("ascii")
    save_owner = owner_id if role == "admin" else group_id
    ok = await _storage.save_binary(tool_id, save_owner, encoded, filename, len(data))
    if not ok:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    flog.info(
        f"Binario subido a tool {tool_id}: {filename} ({len(data)} bytes)",
        username=user,
    )
    return {"ok": True, "binary_filename": filename, "binary_size": len(data)}


@router.get("/{scope}/{tool_id}/binary")
async def download_tool_binary(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Response:
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    if scope == "private":
        await _assert_read_access(tool_id, tl, ctx)

    binary = await _storage.get_binary(scope, tool_id)
    if not binary:
        raise APIError(
            404, "not_found", "Esta tool no tiene binario", extra={"resource": "tool"}
        )
    data = base64.b64decode(binary["binary_b64"])
    filename = binary.get("binary_filename") or "tool_binary"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
