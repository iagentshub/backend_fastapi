"""Rutas de packs: alta directa, consulta, edición y borrado."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from fastapi import Depends, File, Form, UploadFile

from app.api.routes.auth import GroupContext, require_group_session
from app.api.routes.knowledge._router import router
from app.api.routes.knowledge._shared import (
    _PACK_MAX_FILES,
    _catalogued_file_content,
    _content_labels,
    _edited_labels,
    _extract_document,
    _extraction_item_fields,
    _groups,
    _normalize_pack_path,
    _owner,
    _pack_file_is_extractable,
    _pack_kind,
    _pack_reported_size,
    _pack_skip_reason,
    _packs,
    _shares,
    _sync_social_visibility,
)
from app.auth.auth import get_user_role
from app.errors import APIError
from app.models.request_bodies import (
    KnowledgePackEditBody,
    LabelsBody,
)
from app.utils.origin import assert_resource_writable


@router.post("/packs")
async def upload_pack(
    name: str = Form(...),
    description: str = Form(""),
    paths: str = Form(...),
    sizes: str = Form("[]"),
    source_mode: str = Form("upload"),
    labels: str = Form("[]"),
    files: List[UploadFile] = File(...),
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    pack_name = name.strip()
    if not pack_name:
        raise APIError(
            422, "invalid_field", "Nombre requerido", extra={"field": "name"}
        )
    if len(pack_name) > 160 or len(description) > 2000:
        raise APIError(
            422,
            "invalid_field",
            "El nombre o la descripción superan el límite permitido",
            extra={"field": "name" if len(pack_name) > 160 else "description"},
        )
    try:
        raw_paths = json.loads(paths)
        raw_labels = json.loads(labels)
        raw_sizes = json.loads(sizes)
    except (TypeError, ValueError) as exc:
        raise APIError(
            422,
            "invalid_field",
            "Los metadatos del pack no tienen un formato válido",
            extra={"field": "paths"},
        ) from exc
    if not isinstance(raw_paths, list) or len(raw_paths) != len(files):
        raise APIError(
            422,
            "invalid_field",
            "Cada archivo debe incluir su ruta relativa",
            extra={"field": "paths"},
        )
    # ``sync`` fue usado brevemente por un cliente previo. Se normaliza a la
    # única modalidad con contenido: subir y poder resincronizar.
    if source_mode == "sync":
        source_mode = "upload"
    if source_mode not in {"upload", "reference"}:
        raise APIError(
            422,
            "invalid_field",
            "El modo de origen del pack no es válido",
            extra={"field": "source_mode"},
        )
    if not isinstance(raw_sizes, list) or (raw_sizes and len(raw_sizes) != len(files)):
        raise APIError(
            422,
            "invalid_field",
            "Cada referencia debe incluir su tamaño",
            extra={"field": "sizes"},
        )
    if len(files) > _PACK_MAX_FILES:
        raise APIError(
            413,
            "file_too_large",
            "El directorio contiene demasiados archivos",
            extra={"max_files": _PACK_MAX_FILES},
        )
    content_labels = _content_labels(
        {"labels": raw_labels},
        user=user,
        allow_origin=await get_user_role(user) == "admin",
    )
    normalized_paths = [_normalize_pack_path(str(path)) for path in raw_paths]
    if len(normalized_paths) != len(set(normalized_paths)):
        raise APIError(
            422,
            "invalid_field",
            "El directorio contiene rutas duplicadas",
            extra={"field": "paths"},
        )

    accepted: List[Dict[str, Any]] = []
    ignored: List[Dict[str, str]] = []
    total_bytes = 0
    for index, (upload, relative_path) in enumerate(zip(files, normalized_paths)):
        reason = _pack_skip_reason(relative_path)
        if reason:
            ignored.append({"path": relative_path, "reason": reason})
            continue
        raw = await upload.read()
        size = (
            _pack_reported_size(raw_sizes, index, len(raw))
            if source_mode == "reference"
            else len(raw)
        )
        # El peso lo acota `max_request_bytes`, que el administrador decide y el
        # middleware aplica sobre la petición entera. Un techo por fichero
        # escrito aquí volvía a ser un segundo número, más bajo y no
        # configurable, que rechazaba lo que el panel decía aceptar.
        # Ver docs/adr/011-un-solo-limite-de-tamano-y-lo-pone-el-admin.md
        total_bytes += size
        extraction = None
        content = ""
        if source_mode == "reference":
            content = (
                f"Referencia externa del pack: {relative_path}\n"
                f"Tamano: {size} bytes\n"
                "El contenido no se copió a iAgents Hub y no puede ser leído "
                "por el agente."
            )
        elif _pack_file_is_extractable(relative_path):
            extraction = await _extract_document(
                raw, relative_path, upload.content_type or ""
            )
            content = extraction.text
        if not content.strip() or "\x00" in content:
            extraction = None
            content = _catalogued_file_content(
                relative_path, upload.content_type or "", size
            )
        accepted.append(
            {
                "relative_path": relative_path,
                "kind": _pack_kind(relative_path),
                "mime_type": upload.content_type or "",
                "size_bytes": size,
                "checksum": (
                    hashlib.sha256(f"{relative_path}:{size}".encode()).hexdigest()
                    if source_mode == "reference"
                    else hashlib.sha256(raw).hexdigest()
                ),
                "content": content,
                **_extraction_item_fields(extraction),
            }
        )
    if not accepted:
        raise APIError(
            422,
            "document_text_extraction_failed",
            "El directorio no contiene archivos de conocimiento compatibles",
            extra={"ignored": ignored[:100]},
        )
    owner = await _owner(user, group_id) or group_id
    pack = await _packs.create(
        owner_id=owner,
        name=pack_name,
        description=description.strip(),
        labels=content_labels,
        items=accepted,
        source_mode=source_mode,
    )
    await _sync_social_visibility(
        resource_type="knowledge_pack",
        resource_id=str(pack["id"]),
        ctx=ctx,
        is_public="public" in content_labels,
    )
    pack = await _packs.get(str(pack["id"])) or pack
    pack["ignored"] = ignored
    return pack


@router.get("/packs/{pack_id}")
async def get_pack(
    pack_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    pack = await _packs.get(pack_id)
    if pack is None:
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    if (
        not await _shares.is_accessible(
            _groups,
            resource_type="knowledge_pack",
            resource_id=pack_id,
            owner_id=pack.get("owner_id"),
            requester=ctx.user,
            requester_group=ctx.group_id,
        )
        and await get_user_role(ctx.user) != "admin"
    ):
        raise APIError(403, "forbidden", "Sin acceso a este pack")
    return pack


async def _set_pack_active(
    pack_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    pack = await _packs.get(pack_id, include_items=False)
    if pack is None:
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    assert_resource_writable(pack, "knowledge_pack")
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este pack")
    owner = None if role == "admin" else str(pack["owner_id"])
    if not await _packs.set_active(pack_id, owner, active):
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    return await _packs.get(pack_id) or {"id": pack_id, "is_active": active}


@router.post("/packs/{pack_id}/activate")
async def activate_pack(
    pack_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_pack_active(pack_id, True, ctx)


@router.post("/packs/{pack_id}/deactivate")
async def deactivate_pack(
    pack_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_pack_active(pack_id, False, ctx)


@router.delete("/packs/{pack_id}")
async def delete_pack(
    pack_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, bool]:
    pack = await _packs.get(pack_id, include_items=False)
    if pack is None:
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    assert_resource_writable(pack, "knowledge_pack")
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este pack")
    if not await _packs.delete(pack_id, None if role == "admin" else pack["owner_id"]):
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    return {"ok": True}


@router.put("/packs/{pack_id}")
async def update_pack(
    pack_id: str,
    body: KnowledgePackEditBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    pack = await _packs.get(pack_id, include_items=False)
    if pack is None:
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    assert_resource_writable(pack, "knowledge_pack")
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este pack")
    name = str(body.name if body.name is not None else pack.get("name") or "").strip()
    if not name:
        raise APIError(422, "name_required", "El nombre es obligatorio")
    if len(name) > 160:
        raise APIError(
            422, "name_too_long", "El nombre no puede superar 160 caracteres"
        )
    description = str(
        body.description
        if body.description is not None
        else pack.get("description") or ""
    ).strip()
    if len(description) > 2000:
        raise APIError(
            422,
            "description_too_long",
            "La descripción no puede superar 2000 caracteres",
        )
    labels = (
        _edited_labels(body.labels, pack, user=ctx.user)
        if body.labels is not None
        else list(pack.get("labels") or [])
    )
    owner = None if role == "admin" else str(pack["owner_id"])
    if not await _packs.update_metadata(
        pack_id,
        owner,
        name=name,
        description=description,
        labels=labels,
    ):
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    await _sync_social_visibility(
        resource_type="knowledge_pack",
        resource_id=pack_id,
        ctx=ctx,
        is_public="public" in labels,
    )
    return await _packs.get(pack_id) or {"id": pack_id, "name": name}


@router.put("/packs/{pack_id}/labels")
async def update_pack_labels(
    pack_id: str,
    body: LabelsBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    pack = await _packs.get(pack_id, include_items=False)
    if pack is None:
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    assert_resource_writable(pack, "knowledge_pack")
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este pack")
    labels = _edited_labels(body.labels, pack, user=ctx.user)
    owner = None if role == "admin" else str(pack["owner_id"])
    if not await _packs.update_labels(pack_id, owner, labels):
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    await _sync_social_visibility(
        resource_type="knowledge_pack",
        resource_id=pack_id,
        ctx=ctx,
        is_public="public" in labels,
    )
    return await _packs.get(pack_id) or {"id": pack_id, "labels": labels}
