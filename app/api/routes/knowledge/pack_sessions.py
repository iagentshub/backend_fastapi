"""Subida de un pack por sesión: un pack entero no cabe en una petición.

El cliente abre una sesión, sube los ficheros de uno en uno y la cierra. Los
límites son los de sesión (`_PACK_SESSION_MAX_*`), más altos que los de la
subida directa porque aquí no hay una sola petición que sostener en memoria.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Dict

from fastapi import Depends, File, Form, UploadFile

from app.api.routes.auth import GroupContext, require_group_session
from app.api.routes.knowledge._router import router
from app.api.routes.knowledge._shared import (
    _PACK_MAX_FILE_BYTES,
    _PACK_SESSION_MAX_FILES,
    _PACK_SESSION_MAX_TOTAL_BYTES,
    _catalogued_file_content,
    _content_labels,
    _normalize_pack_path,
    _owner,
    _pack_file_is_extractable,
    _pack_kind,
    _pack_skip_reason,
    _packs,
    _sync_social_visibility,
    _valid_sha256,
)
from app.auth.auth import get_user_role
from app.errors import APIError
from app.models.request_bodies import (
    KnowledgePackUploadSessionBody,
)
from app.storage.guest import is_guest
from app.storage.knowledge import (
    extract_document_text,
)
from app.utils import flog


@router.post("/packs/upload-sessions")
async def create_pack_upload_session(
    body: KnowledgePackUploadSessionBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    if is_guest(ctx.user):
        raise APIError(403, "forbidden", "Los invitados no pueden crear packs")
    name = str(body.name or "").strip()
    description = str(body.description or "").strip()
    total_files = int(body.total_files or 0)
    if not name or len(name) > 160 or len(description) > 2000:
        raise APIError(
            422,
            "invalid_field",
            "El nombre o la descripción no son válidos",
            extra={"field": "name"},
        )
    if total_files < 1 or total_files > _PACK_SESSION_MAX_FILES:
        raise APIError(
            422,
            "invalid_field",
            "El número de archivos del pack no es válido",
            extra={"field": "total_files", "max_files": _PACK_SESSION_MAX_FILES},
        )
    source_mode = "reference" if body.source_mode == "reference" else "upload"
    labels = _content_labels(
        {"labels": body.labels or []},
        allow_origin=await get_user_role(ctx.user) == "admin",
    )
    owner = await _owner(ctx.user, ctx.group_id) or ctx.group_id
    return await _packs.create(
        owner_id=owner,
        name=name,
        description=description,
        labels=labels,
        items=[],
        source_mode=source_mode,
        upload_status="uploading",
    )

@router.post("/packs/upload-sessions/{pack_id}/files")
async def upload_pack_session_file(
    pack_id: str,
    relative_path: str = Form(...),
    reported_size: int = Form(0),
    reported_checksum: str = Form(""),
    reported_mime_type: str = Form(""),
    reported_modified_at: int | None = Form(None),
    file: UploadFile = File(...),
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    pack = await _packs.get(pack_id, include_items=False)
    if pack is None or pack.get("upload_status") != "uploading":
        raise APIError(
            404,
            "not_found",
            "Sesión no encontrada",
            extra={"resource": "knowledge_pack"},
        )
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre esta sesión")
    path = _normalize_pack_path(relative_path)
    reason = _pack_skip_reason(path)
    if reason:
        raise APIError(
            422,
            "invalid_field",
            "El archivo se ha omitido por seguridad",
            extra={"field": "file", "reason": reason},
        )
    raw = await file.read()
    source_mode = str(pack.get("source_mode") or "upload")
    size = reported_size if source_mode == "reference" else len(raw)
    if size < 0 or size > _PACK_MAX_FILE_BYTES:
        raise APIError(
            413,
            "file_too_large",
            "El archivo supera el límite de 10 MB",
            extra={"max_file_mb": 10},
        )
    if int(pack.get("size_bytes") or 0) + size > _PACK_SESSION_MAX_TOTAL_BYTES:
        raise APIError(
            413,
            "file_too_large",
            "El pack supera el límite total de 500 MB",
            extra={"max_total_mb": 500},
        )
    client_checksum = reported_checksum.strip().lower()
    if client_checksum and not _valid_sha256(client_checksum):
        raise APIError(
            422,
            "invalid_field",
            "El checksum calculado por el dispositivo no es válido",
            extra={"field": "checksum", "path": path},
        )
    mime_type = (reported_mime_type.strip().lower() or file.content_type or "")[:255]
    if source_mode == "reference":
        content = (
            f"Referencia externa del pack: {path}\nTamano: {size} bytes\n"
            "El contenido no se copió a iAgents Hub."
        )
        checksum = (
            client_checksum or hashlib.sha256(f"{path}:{size}".encode()).hexdigest()
        )
    else:
        server_checksum = hashlib.sha256(raw).hexdigest()
        if client_checksum and server_checksum != client_checksum:
            raise APIError(
                422,
                "invalid_field",
                "El archivo recibido no coincide con su checksum",
                extra={"field": "checksum", "path": path},
            )
        content = ""
        if _pack_file_is_extractable(path):
            try:
                content = await asyncio.to_thread(
                    extract_document_text, raw, path, mime_type
                )
            except Exception as exc:  # noqa: BLE001
                # Ancho a propósito: extract_document_text envuelve parsers de
                # terceros (PDF, ofimática, OCR) y un fichero raro no puede tumbar
                # la subida. Lo que no puede es no dejar rastro: sin esto el
                # usuario sube un PDF, recibe la ficha catalogada en vez del texto
                # y no hay forma de saber por qué.
                flog.warning(
                    f"[knowledge] Extracción fallida de {path} "
                    f"({mime_type}): {exc}"
                )
                content = ""
        if not content.strip() or "\x00" in content:
            content = _catalogued_file_content(path, mime_type, size)
        checksum = server_checksum
    owner = str(pack["owner_id"])
    result = await _packs.upsert_item(
        pack_id,
        owner,
        {
            "relative_path": path,
            "kind": _pack_kind(path),
            "mime_type": mime_type,
            "size_bytes": size,
            "checksum": checksum,
            "content": content,
        },
    )
    if result is None:
        raise APIError(404, "not_found", "Sesión no encontrada")
    return result

@router.post("/packs/upload-sessions/{pack_id}/complete")
async def complete_pack_upload_session(
    pack_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    pack = await _packs.get(pack_id, include_items=False)
    if pack is None:
        raise APIError(404, "not_found", "Sesión no encontrada")
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre esta sesión")
    completed = await _packs.complete_upload(pack_id, str(pack["owner_id"]))
    if completed is None:
        raise APIError(
            422,
            "invalid_field",
            "No se puede finalizar un pack sin archivos correctos",
            extra={"field": "files"},
        )
    await _sync_social_visibility(
        resource_type="knowledge_pack",
        resource_id=pack_id,
        ctx=ctx,
        is_public="public" in (completed.get("labels") or []),
    )
    return completed

@router.delete("/packs/upload-sessions/{pack_id}")
async def cancel_pack_upload_session(
    pack_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, bool]:
    pack = await _packs.get(pack_id, include_items=False)
    if pack is None:
        return {"ok": True}
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre esta sesión")
    await _packs.delete(pack_id, None if role == "admin" else str(pack["owner_id"]))
    return {"ok": True}
