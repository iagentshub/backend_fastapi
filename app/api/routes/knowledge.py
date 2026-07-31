"""Rutas de conocimiento: URLs y documentos."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.api.routes.auth import GroupContext, require_group
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, DB_FILE
from app.errors import APIError
from app.storage.folders import VALID_SECTIONS, FolderStorage
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.guest import get_session, is_guest
from app.storage.knowledge import (
    KnowledgeStorage,
    extract_document_text,
    fetch_url_text,
)
from app.storage.storage import AgentStorage

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_storage = KnowledgeStorage(DB_FILE)
_agents = AgentStorage(AGENTS_DIR)
_shares = GroupShareStorage(DB_FILE)
_groups = GroupStorage(DB_FILE)
_folders = FolderStorage()

_ALLOWED_EXTS = {".txt", ".md", ".pdf"}


async def _owner(user: str, group_id: str) -> Optional[str]:
    return None if await get_user_role(user) == "admin" else group_id


def _guest_item(*, type: str, title: str, source: str, content: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": uuid4().hex[:16],
        "type": type,
        "title": title,
        "source": source,
        "content": content,
        "char_count": len(content),
        "created_at": now,
        "updated_at": now,
    }


# ── Item endpoints ─────────────────────────────────────────────────────────────


@router.get("", response_model=List[Dict[str, Any]])
async def list_items(
    type: Optional[str] = None,
    owner_scope: str = "group",
    requested_group_id: Optional[str] = Query(None, alias="group_id"),
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    user = ctx.user
    owner_id = user if owner_scope == "personal" else ctx.group_id
    if is_guest(user):
        items = get_session(user).knowledge
        filtered = [i for i in items if not type or i["type"] == type]
        if offset:
            filtered = filtered[offset:]
        if limit:
            filtered = filtered[:limit]
        return filtered
    role = await get_user_role(user)
    if requested_group_id is not None:
        # Filtro por grupo: se aplica siempre, incluido admin
        if role != "admin" and not await _groups.can_access(requested_group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        shared_ids = set(
            await _shares.get_group_shared_resource_ids(
                requested_group_id, "knowledge"
            )
        )
        items: List[Dict[str, Any]] = []
        for kid in shared_ids:
            k = await _storage.get(kid)
            if (
                k
                and (not type or k.get("type") == type)
                and await _groups.owner_is_active(k.get("owner_id") or "")
            ):
                k["_shared"] = True
                k["_group_id"] = requested_group_id
                items.append(k)
    else:
        # (incluye admin: la visibilidad global de admin se sirve vía /api/admin/*,
        # listar por owner_id=None aquí exponía knowledge privado de otros usuarios
        # sin marcarlo como ajeno)
        items = await _storage.list(owner_id, type)
        own_ids = {i["id"] for i in items}
        # Acumula shares de todos los grupos del usuario
        user_groups = await _groups.list_for_user(user)
        shared_map: Dict[str, str] = {}  # resource_id -> group_id
        for group in user_groups:
            gid = group["id"]
            for rid in await _shares.get_group_shared_resource_ids(
                gid, "knowledge"
            ):
                if rid not in shared_map:
                    shared_map[rid] = gid
        extra: List[Dict[str, Any]] = []
        for kid, gid in shared_map.items():
            if kid not in own_ids:
                k = await _storage.get(kid)
                if (
                    k
                    and (not type or k.get("type") == type)
                    and await _groups.owner_is_active(k.get("owner_id") or "")
                ):
                    k["_shared"] = True
                    k["_group_id"] = gid
                    extra.append(k)
        items = items + extra
    permission_group_id = requested_group_id or ctx.group_id
    if permission_group_id != user and role != "admin":
        items = [
            item for item in items
            if await _groups.has_resource_permission(
                permission_group_id, user, "knowledge", item["id"], "view"
            )
        ]
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    return await _folders.enrich_items(items, default_owner=owner_id)


@router.post("/text")
async def add_text(
    request: Request,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    body = await request.json()
    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "").strip()
    source = str(body.get("source") or title).strip()
    if not title:
        raise APIError(422, "invalid_field", "Título requerido", extra={"field": "title"})
    if not content:
        raise APIError(422, "invalid_field", "Contenido requerido", extra={"field": "content"})
    if is_guest(user):
        item = _guest_item(type="text", title=title, source=source, content=content)
        get_session(user).knowledge.append(item)
        return item
    owner = await _owner(user, group_id) or group_id
    return await _storage.save(
        type="text", title=title, source=source, content=content, owner_id=owner
    )


@router.post("/url")
async def add_url(
    request: Request,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    body = await request.json()
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip() or url
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
    if is_guest(user):
        item = _guest_item(type="url", title=title, source=url, content=content)
        get_session(user).knowledge.append(item)
        return item
    owner = await _owner(user, group_id) or group_id
    return await _storage.save(
        type="url", title=title, source=url, content=content, owner_id=owner
    )


@router.post("/document")
async def upload_document(
    file: UploadFile = File(...),
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    filename = file.filename or "documento"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXTS:
        raise APIError(
            422,
            "unsupported_document_format",
            f"Formato no soportado. Formatos permitidos: {', '.join(_ALLOWED_EXTS)}",
            extra={"allowed_extensions": sorted(_ALLOWED_EXTS)},
        )
    content_bytes = await file.read()
    if len(content_bytes) > 10 * 1024 * 1024:
        raise APIError(
            413, "file_too_large", "El fichero supera el límite de 10 MB",
            extra={"max_mb": 10},
        )
    try:
        # extract_document_text usa pypdf (síncrono/bloqueante en PDFs grandes)
        # → se ejecuta en un thread para no bloquear el event loop
        import asyncio as _asyncio

        content = await _asyncio.to_thread(
            extract_document_text, content_bytes, filename, file.content_type or ""
        )
    except Exception as exc:
        raise APIError(422, "document_extraction_failed", str(exc)) from exc
    if not content.strip():
        raise APIError(
            422, "document_text_extraction_failed", "No se pudo extraer texto del documento"
        )
    if is_guest(user):
        item = _guest_item(
            type="document", title=filename, source=filename, content=content
        )
        get_session(user).knowledge.append(item)
        return item
    owner = await _owner(user, group_id) or group_id
    return await _storage.save(
        type="document",
        title=filename,
        source=filename,
        content=content,
        owner_id=owner,
    )


# ── Folders ────────────────────────────────────────────────────────────────


@router.get("/folders")
async def list_folders(
    section: str = Query(...),
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    if section not in VALID_SECTIONS:
        raise APIError(422, "invalid_field", "Sección de carpeta no válida", extra={"field": "section"})
    if is_guest(ctx.user):
        return []
    return await _folders.list(ctx.group_id, section)


@router.post("/folders")
async def create_folder(
    request: Request,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    if is_guest(ctx.user):
        raise APIError(403, "forbidden", "Los invitados no pueden crear carpetas")
    body = await request.json()
    section = str(body.get("section") or "").strip()
    name = str(body.get("name") or "").strip()
    if section not in VALID_SECTIONS or not name:
        raise APIError(422, "folder_fields_required", "Sección y nombre son obligatorios")
    try:
        return await _folders.create(ctx.group_id, section, name[:80])
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise APIError(
                409, "already_exists", "Ya existe una carpeta con ese nombre",
                extra={"resource": "folder"},
            ) from exc
        raise


@router.patch("/folders/{folder_id}")
async def update_folder(
    folder_id: str,
    request: Request,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    body = await request.json()
    name = body.get("name")
    if name is not None:
        name = str(name).strip()[:80]
        if not name:
            raise APIError(422, "invalid_field", "Nombre obligatorio", extra={"field": "name"})
    folder = await _folders.update(folder_id, ctx.group_id, name=name)
    if not folder:
        raise APIError(404, "not_found", "Carpeta no encontrada", extra={"resource": "folder"})
    return folder


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    cascade: bool = Query(False),
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, bool]:
    if not await _folders.delete(folder_id, ctx.group_id, cascade):
        raise APIError(404, "not_found", "Carpeta no encontrada", extra={"resource": "folder"})
    return {"ok": True}


@router.patch("/{item_id}")
async def update_item_folder(
    item_id: str,
    request: Request,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    item = await _storage.get(item_id, await _owner(ctx.user, ctx.group_id))
    if not item:
        raise APIError(404, "not_found", "Item no encontrado", extra={"resource": "item"})
    body = await request.json()
    try:
        await _folders.assign(
            ctx.group_id, str(item.get("type") or "document"), item_id,
            str(body["folder_id"]) if body.get("folder_id") else None,
        )
    except ValueError as exc:
        raise APIError(422, "folder_resource_mismatch", str(exc)) from exc
    return {
        **item,
        "folder_id": await _folders.folder_for(
            ctx.group_id, str(item.get("type") or "document"), item_id
        ),
    }


@router.delete("/{item_id}")
async def delete_item(
    item_id: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, bool]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        s = get_session(user)
        before = len(s.knowledge)
        s.knowledge = [i for i in s.knowledge if i["id"] != item_id]
        if len(s.knowledge) == before:
            raise APIError(404, "not_found", "Item no encontrado", extra={"resource": "item"})
        return {"ok": True}
    owner = await _owner(user, group_id)
    item = await _storage.get(item_id, owner)
    if not item or not await _storage.delete(item_id, owner):
        raise APIError(404, "not_found", "Item no encontrado", extra={"resource": "item"})
    await _folders.remove_resource(
        str(item.get("owner_id") or group_id),
        str(item.get("type") or "document"),
        item_id,
    )
    return {"ok": True}
