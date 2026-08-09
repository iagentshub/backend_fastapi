"""Rutas de conocimiento: URLs y documentos."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile

from app.api.pagination import paginar
from app.api.routes.auth import GroupContext, require_group, require_group_session
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR
from app.errors import APIError
from app.storage.agent_storage import AgentStorage
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.guest import get_session, is_guest
from app.storage.knowledge import (
    KnowledgeStorage,
    extract_document_text,
    fetch_url_text,
)
from app.storage.skill_storage import SKILL_ASSIGNABLE_LABELS
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.net import json_body

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_storage = KnowledgeStorage()
_agents = AgentStorage(AGENTS_DIR)
_shares = GroupShareStorage()
_groups = GroupStorage()

_ALLOWED_EXTS = {".txt", ".md", ".pdf"}


async def _owner(user: str, group_id: str) -> Optional[str]:
    return None if await get_user_role(user) == "admin" else group_id


def _content_labels(body: Dict[str, Any]) -> List[str]:
    raw = body.get("labels")
    if raw is None:
        return ["private"]
    if not isinstance(raw, list):
        raise APIError(
            422,
            "invalid_field",
            "Las labels deben ser una lista del catálogo del sistema",
            extra={"field": "labels"},
        )
    labels = list(
        dict.fromkeys(str(value).strip() for value in raw if str(value).strip())
    )
    invalid = [value for value in labels if value not in SKILL_ASSIGNABLE_LABELS]
    if invalid:
        raise APIError(
            422,
            "invalid_field",
            "Knowledge contiene labels fuera del catálogo del sistema",
            extra={"field": "labels", "invalid": invalid},
        )
    labels = [value for value in labels if value not in {"public", "private"}]
    return ["private", *labels]


def _guest_item(
    *, type: str, title: str, source: str, content: str, labels: List[str]
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": generate_id(16),
        "name": title,
        "resource_type": "knowledge",
        "description": "",
        "icon": "",
        "scope": "private",
        "labels": labels,
        "is_active": True,
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
    include_inactive: bool = False,
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    user = ctx.user
    owner_id = user if owner_scope == "personal" else ctx.group_id
    if is_guest(user):
        items = get_session(user).knowledge
        filtered = [i for i in items if not type or i["type"] == type]
        filtered = paginar(filtered, limit, offset, response)
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
    if not include_inactive:
        items = [i for i in items if i.get("is_active", True)]
    items = paginar(items, limit, offset, response)
    return items


@router.post("/text")
async def add_text(
    request: Request,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    body = await json_body(request)
    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "").strip()
    source = str(body.get("source") or title).strip()
    labels = _content_labels(body)
    if not title:
        raise APIError(422, "invalid_field", "Título requerido", extra={"field": "title"})
    if not content:
        raise APIError(422, "invalid_field", "Contenido requerido", extra={"field": "content"})
    if is_guest(user):
        item = _guest_item(
            type="text", title=title, source=source, content=content, labels=labels
        )
        get_session(user).knowledge.append(item)
        return item
    owner = await _owner(user, group_id) or group_id
    return await _storage.save(
        type="text",
        title=title,
        source=source,
        content=content,
        owner_id=owner,
        labels=labels,
    )


@router.post("/url")
async def add_url(
    request: Request,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    body = await json_body(request)
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip() or url
    labels = _content_labels(body)
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
        item = _guest_item(
            type="url", title=title, source=url, content=content, labels=labels
        )
        get_session(user).knowledge.append(item)
        return item
    owner = await _owner(user, group_id) or group_id
    return await _storage.save(
        type="url",
        title=title,
        source=url,
        content=content,
        owner_id=owner,
        labels=labels,
    )


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
    content_labels = _content_labels({"labels": parsed_labels})
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
            type="document",
            title=filename,
            source=filename,
            content=content,
            labels=content_labels,
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
        labels=content_labels,
    )


@router.delete("/{item_id}")
async def delete_item(
    item_id: str,
    ctx: GroupContext = Depends(require_group_session),
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
    if not await _storage.delete(item_id, owner):
        raise APIError(404, "not_found", "Item no encontrado", extra={"resource": "item"})
    return {"ok": True}


async def _set_knowledge_active(
    item_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        raise APIError(
            403, "forbidden", "Los invitados no pueden desactivar conocimiento"
        )
    owner = await _owner(user, group_id)
    if not await _storage.set_active(item_id, owner, active):
        raise APIError(404, "not_found", "Item no encontrado", extra={"resource": "item"})
    estado = "activado" if active else "desactivado"
    flog.info(f"Conocimiento {estado}: {item_id}", username=user)
    return {"ok": True, "is_active": active}


@router.post("/{item_id}/activate")
async def activate_item(
    item_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_knowledge_active(item_id, True, ctx)


@router.post("/{item_id}/deactivate")
async def deactivate_item(
    item_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_knowledge_active(item_id, False, ctx)
