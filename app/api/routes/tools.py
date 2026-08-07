"""Rutas de tools (herramientas ejecutables).

Fase 1: solo catalogación y asignación a agentes — sin motor de ejecución.
Calcado de skills.py, con dos diferencias estructurales: `language`
(obligatorio) sustituye a `category`, y hay contenido dual texto/binario
(python/shell usan `content`; cpp usa un binario subido aparte).

A diferencia de skills.py/prompts.py, estas rutas NO aceptan invitados
(`require_group` en vez de `require_group_session`): GuestSession solo
contempla connections/agents/skills/knowledge/memory/prompts por diseño (ver
comentario en auth.py sobre el allowlist de invitado), y subir binarios
ejecutables desde una sesión de demo efímera no aporta nada al producto.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from app.api.routes.auth import GroupContext, require_group
from app.auth.auth import get_user_role
from app.errors import APIError
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.storage import SKILL_ASSIGNABLE_LABELS, TOOL_LANGUAGES, ToolStorage
from app.utils import flog
from app.utils.net import json_body
from app.utils.origin import compute_origin_type

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


async def _assert_read_access(tool_id: str, tl: Dict[str, Any], ctx: GroupContext) -> None:
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
    include_inactive: bool = False,
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    user = ctx.user
    _check_scope(scope)
    items = await _storage.list(scope)
    role = await get_user_role(user)
    if group_id is not None:
        # Filtro por grupo: se aplica siempre, incluido admin
        if role != "admin" and not await _groups.can_access(group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        shared_ids = set(await _shares.get_group_shared_resource_ids(group_id, "tool"))
        items = [tl for tl in items if tl["id"] in shared_ids]
        for tl in items:
            tl["_shared"] = True
            tl["_group_id"] = group_id
    else:
        # Tools propias (personales o del group activo) + públicas + legacy sin owner
        # + shares de todos los grupos del usuario.
        group_id = ctx.group_id
        items = [
            tl
            for tl in items
            if tl.get("scope") == "public"
            or tl.get("owner_id") is None
            or tl.get("owner_id") == user
            or tl.get("owner_id") == group_id
        ]
        own_ids = {tl["id"] for tl in items}
        user_groups = await _groups.list_for_user(user)
        shared_map: Dict[str, str] = {}  # resource_id -> group_id
        for group in user_groups:
            gid = group["id"]
            for rid in await _shares.get_group_shared_resource_ids(gid, "tool"):
                if rid not in shared_map:
                    shared_map[rid] = gid
        for tid in set(shared_map.keys()) - own_ids:
            tl = await _storage.get_any(tid)
            if tl and await _groups.owner_is_active(tl.get("owner_id") or ""):
                tl["_shared"] = True
                tl["_group_id"] = shared_map[tid]
                items.append(tl)
    if not include_inactive:
        items = [tl for tl in items if tl.get("is_active", True)]
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    for tl in items:
        _mark_origin(tl, user, ctx.group_id)
        # Defensivo: list() ya usa include_content=False (sin binary_b64 en
        # el dict), pero se mantiene el pop explícito — mismo precedente que
        # admin_list_skills con "content".
        tl.pop("binary_b64", None)
    return items


@router.get("/{scope}/{tool_id}")
async def get_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user = ctx.user
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(404, "not_found", "Tool no encontrada", extra={"resource": "tool"})

    if scope == "private":
        await _assert_read_access(tool_id, tl, ctx)

    _mark_origin(tl, user, ctx.group_id)
    tl.pop("binary_b64", None)
    return tl


@router.post("/{scope}")
async def save_tool(
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
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    # Ownership check before delete
    tl = await _storage.get_any(tool_id)
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
    flog.info(
        f"Tool borrada: {tool_id} {(tl or {}).get('name', '')!r}", username=user
    )
    return {"ok": True}


async def _set_tool_active(
    tool_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    tl = await _storage.get_any(tool_id)
    if not tl:
        raise APIError(404, "not_found", "Tool no encontrada", extra={"resource": "tool"})
    role = await get_user_role(user)
    if role != "admin" and tl.get("owner_id") not in (group_id, None):
        raise APIError(403, "forbidden", "Solo el propietario puede cambiar el estado")
    owner = None if role == "admin" else group_id
    if not await _storage.set_active(tool_id, owner, active):
        raise APIError(404, "not_found", "Tool no encontrada", extra={"resource": "tool"})
    estado = "activada" if active else "desactivada"
    flog.info(f"Tool {estado}: {tool_id} {tl.get('name', '')!r}", username=user)
    return {"ok": True, "is_active": active}


@router.post("/{tool_id}/activate")
async def activate_tool(
    tool_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_tool_active(tool_id, True, ctx)


@router.post("/{tool_id}/deactivate")
async def deactivate_tool(
    tool_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_tool_active(tool_id, False, ctx)


# ── Binario (solo tools cpp) — subida/descarga en dos pasos ─────────────────
# Mismo patrón que auth.py::upload_avatar / users.py::get_avatar: JSON de
# metadatos primero (POST /api/tools/{scope}), binario aparte.


@router.post("/{scope}/{tool_id}/binary")
async def upload_tool_binary(
    scope: str,
    tool_id: str,
    file: UploadFile = File(...),
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(404, "not_found", "Tool no encontrada", extra={"resource": "tool"})
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
        raise APIError(404, "not_found", "Tool no encontrada", extra={"resource": "tool"})
    flog.info(
        f"Binario subido a tool {tool_id}: {filename} ({len(data)} bytes)",
        username=user,
    )
    return {"ok": True, "binary_filename": filename, "binary_size": len(data)}


@router.get("/{scope}/{tool_id}/binary")
async def download_tool_binary(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group)
) -> Response:
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(404, "not_found", "Tool no encontrada", extra={"resource": "tool"})
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
