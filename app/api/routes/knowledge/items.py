"""Rutas de items de conocimiento: texto, URL y documento suelto."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from fastapi import Depends, File, Form, Query, Response, UploadFile

from app.api.routes.auth import GroupContext, require_group_session
from app.api.routes.knowledge._router import router
from app.api.routes.knowledge._shared import (
    _catalogued_file_content,
    _content_labels,
    _edited_labels,
    _owner,
    _pack_file_is_extractable,
    _pack_skip_reason,
    _storage,
    _sync_social_visibility,
)
from app.auth.auth import get_user_role
from app.errors import APIError
from app.models.request_bodies import (
    KnowledgeEditBody,
    KnowledgeTextBody,
    KnowledgeUrlBody,
    LabelsBody,
)
from app.pagination.models import OffsetParams
from app.services.knowledge_listing import list_authenticated_knowledge
from app.storage.knowledge import (
    extract_document_text,
    fetch_url_text,
)
from app.utils import flog
from app.utils.origin import assert_resource_writable


@router.get("", response_model=List[Dict[str, Any]])
async def list_items(
    type: Optional[str] = None,
    owner_scope: str = "group",
    requested_group_id: Optional[str] = Query(None, alias="group_id"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    return await list_authenticated_knowledge(
        _storage,
        ctx=ctx,
        owner_scope=owner_scope,
        type=type,
        page=OffsetParams(limit=limit, offset=offset),
        response=response,
        requested_group_id=requested_group_id,
    )


@router.post("/text")
async def add_text(
    body: KnowledgeTextBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    body = body.payload()
    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "").strip()
    source = str(body.get("source") or title).strip()
    labels = _content_labels(
        body,
        user=user,
        allow_origin=await get_user_role(user) == "admin",
    )
    if not title:
        raise APIError(
            422, "invalid_field", "Título requerido", extra={"field": "title"}
        )
    if not content:
        raise APIError(
            422, "invalid_field", "Contenido requerido", extra={"field": "content"}
        )
    owner = await _owner(user, group_id) or group_id
    item = await _storage.save(
        type="text",
        title=title,
        source=source,
        content=content,
        owner_id=owner,
        labels=labels,
    )
    await _sync_social_visibility(
        resource_type="knowledge",
        resource_id=str(item["id"]),
        ctx=ctx,
        is_public="public" in labels,
    )
    return item


@router.post("/url")
async def add_url(
    body: KnowledgeUrlBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    body = body.payload()
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip() or url
    labels = _content_labels(
        body,
        user=user,
        allow_origin=await get_user_role(user) == "admin",
    )
    if not url:
        raise APIError(422, "invalid_field", "URL requerida", extra={"field": "url"})
    try:
        content = await asyncio.to_thread(fetch_url_text, url)
    except Exception as exc:
        raise APIError(
            422, "url_fetch_failed", f"No se pudo obtener la URL: {exc}"
        ) from exc
    if not content.strip():
        raise APIError(
            422, "url_text_extraction_failed", "No se pudo extraer texto de la URL"
        )
    owner = await _owner(user, group_id) or group_id
    item = await _storage.save(
        type="url",
        title=title,
        source=url,
        content=content,
        owner_id=owner,
        labels=labels,
    )
    await _sync_social_visibility(
        resource_type="knowledge",
        resource_id=str(item["id"]),
        ctx=ctx,
        is_public="public" in labels,
    )
    return item


@router.post("/document")
async def upload_document(
    file: UploadFile = File(...),
    labels: str = Form("[]"),
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    try:
        parsed_labels = json.loads(labels)
    except (TypeError, ValueError) as exc:
        raise APIError(
            422,
            "invalid_field",
            "Las labels del documento no tienen un formato válido",
            extra={"field": "labels"},
        ) from exc
    content_labels = _content_labels(
        {"labels": parsed_labels},
        user=user,
        allow_origin=await get_user_role(user) == "admin",
    )
    filename = file.filename or "documento"
    unsafe_reason = _pack_skip_reason(PurePosixPath(filename).name)
    if unsafe_reason:
        raise APIError(
            422,
            "unsupported_document_format",
            "El fichero parece contener credenciales o secretos y no se puede importar",
            extra={"reason": unsafe_reason},
        )
    content_bytes = await file.read()
    if len(content_bytes) > 10 * 1024 * 1024:
        raise APIError(
            413,
            "file_too_large",
            "El fichero supera el límite de 10 MB",
            extra={"max_mb": 10},
        )
    content = ""
    if _pack_file_is_extractable(filename):
        try:
            content = await asyncio.to_thread(
                extract_document_text,
                content_bytes,
                filename,
                file.content_type or "",
            )
        except Exception as exc:  # noqa: BLE001
            # Ancho a propósito: extract_document_text envuelve parsers de
            # terceros (PDF, ofimática, OCR) y un fichero raro no puede tumbar
            # la subida. Lo que no puede es no dejar rastro: sin esto el
            # usuario sube un PDF, recibe la ficha catalogada en vez del texto
            # y no hay forma de saber por qué.
            flog.warning(
                f"[knowledge] Extracción fallida de {filename} "
                f"({file.content_type}): {exc}"
            )
            content = ""
    if not content.strip() or "\x00" in content:
        content = _catalogued_file_content(
            filename, file.content_type or "", len(content_bytes)
        )
    owner = await _owner(user, group_id) or group_id
    item = await _storage.save(
        type="document",
        title=filename,
        source=filename,
        content=content,
        owner_id=owner,
        labels=content_labels,
        mime_type=file.content_type or "",
        size_bytes=len(content_bytes),
        checksum=hashlib.sha256(content_bytes).hexdigest(),
    )
    await _sync_social_visibility(
        resource_type="knowledge",
        resource_id=str(item["id"]),
        ctx=ctx,
        is_public="public" in content_labels,
    )
    return item


@router.put("/{item_id}")
async def update_item(
    item_id: str,
    body: KnowledgeEditBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    item = await _storage.get(item_id)
    if item is None:
        raise APIError(
            404, "not_found", "Item no encontrado", extra={"resource": "item"}
        )
    assert_resource_writable(item, "knowledge")
    role = await get_user_role(ctx.user)
    if role != "admin" and item.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este conocimiento")
    name = str(body.name if body.name is not None else item.get("title") or "").strip()
    if not name:
        raise APIError(422, "name_required", "El nombre es obligatorio")
    if len(name) > 160:
        raise APIError(
            422, "name_too_long", "El nombre no puede superar 160 caracteres"
        )
    labels = (
        _edited_labels(body.labels, item, user=ctx.user)
        if body.labels is not None
        else list(item.get("labels") or [])
    )
    owner = None if role == "admin" else str(item["owner_id"])
    if not await _storage.update_metadata(item_id, owner, title=name, labels=labels):
        raise APIError(
            404, "not_found", "Item no encontrado", extra={"resource": "item"}
        )
    await _sync_social_visibility(
        resource_type="knowledge",
        resource_id=item_id,
        ctx=ctx,
        is_public="public" in labels,
    )
    return await _storage.get(item_id) or {"id": item_id, "name": name}


@router.put("/{item_id}/labels")
async def update_item_labels(
    item_id: str,
    body: LabelsBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    item = await _storage.get(item_id)
    if item is None:
        raise APIError(
            404, "not_found", "Item no encontrado", extra={"resource": "item"}
        )
    assert_resource_writable(item, "knowledge")
    role = await get_user_role(ctx.user)
    if role != "admin" and item.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este conocimiento")
    labels = _edited_labels(body.labels, item, user=ctx.user)
    owner = None if role == "admin" else str(item["owner_id"])
    if not await _storage.update_labels(item_id, owner, labels):
        raise APIError(
            404, "not_found", "Item no encontrado", extra={"resource": "item"}
        )
    await _sync_social_visibility(
        resource_type="knowledge",
        resource_id=item_id,
        ctx=ctx,
        is_public="public" in labels,
    )
    return await _storage.get(item_id) or {"id": item_id, "labels": labels}


@router.delete("/{item_id}")
async def delete_item(
    item_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, bool]:
    user, group_id = ctx.user, ctx.group_id
    item = await _storage.get(item_id)
    if item:
        assert_resource_writable(item, "knowledge")
    owner = await _owner(user, group_id)
    if not await _storage.delete(item_id, owner):
        raise APIError(
            404, "not_found", "Item no encontrado", extra={"resource": "item"}
        )
    await _sync_social_visibility(
        resource_type="knowledge",
        resource_id=item_id,
        ctx=ctx,
        is_public=False,
    )
    return {"ok": True}


async def _set_item_active(
    item_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    item = await _storage.get(item_id)
    if item is None:
        raise APIError(
            404, "not_found", "Item no encontrado", extra={"resource": "item"}
        )
    assert_resource_writable(item, "knowledge")
    role = await get_user_role(ctx.user)
    if role != "admin" and item.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este conocimiento")
    owner = None if role == "admin" else str(item["owner_id"])
    if not await _storage.set_active(item_id, owner, active):
        raise APIError(
            404, "not_found", "Item no encontrado", extra={"resource": "item"}
        )
    return await _storage.get(item_id) or {"id": item_id, "is_active": active}


@router.post("/{item_id}/activate")
async def activate_item(
    item_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_item_active(item_id, True, ctx)


@router.post("/{item_id}/deactivate")
async def deactivate_item(
    item_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_item_active(item_id, False, ctx)
