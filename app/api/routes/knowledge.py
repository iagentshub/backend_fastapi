"""Rutas de conocimiento: URLs, documentos y carpetas."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.storage.guest import get_session, is_guest
from app.storage.knowledge import FolderStorage, KnowledgeStorage, extract_document_text, fetch_url_text
from app.storage.teams import TeamStorage as _TeamStorage

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_storage = KnowledgeStorage(DB_FILE)
_folders = FolderStorage(DB_FILE)
_sharing_ts = _TeamStorage(DB_FILE)

_ALLOWED_EXTS = {".txt", ".md", ".pdf"}
_VALID_SECTIONS = {"document", "url", "skill", "agents", "memory"}


def _owner(user: str) -> Optional[str]:
    return None if get_user_role(user) == "admin" else user


def _guest_item(*, type: str, title: str, source: str, content: str, folder_id: Optional[str] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": uuid4().hex[:16],
        "type": type,
        "title": title,
        "source": source,
        "content": content,
        "char_count": len(content),
        "folder_id": folder_id,
        "created_at": now,
        "updated_at": now,
    }


# ── Folder endpoints ───────────────────────────────────────────────────────────

class FolderCreate(BaseModel):
    section: str
    name: str


class FolderRename(BaseModel):
    name: str


@router.get("/folders", response_model=List[Dict[str, Any]])
async def list_folders(
    section: Optional[str] = None,
    user: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    if is_guest(user):
        return []
    return _folders.list(_owner(user) or user, section)


@router.post("/folders", response_model=Dict[str, Any])
async def create_folder(
    body: FolderCreate,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden crear carpetas")
    if body.section not in _VALID_SECTIONS:
        raise HTTPException(status_code=422, detail=f"Sección no válida: {body.section}")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío")
    return _folders.create(_owner(user) or user, body.section, name)


@router.patch("/folders/{folder_id}", response_model=Dict[str, Any])
async def rename_folder(
    folder_id: str,
    body: FolderRename,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden renombrar carpetas")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío")
    result = _folders.rename(folder_id, _owner(user) or user, name)
    if result is None:
        raise HTTPException(status_code=404, detail="Carpeta no encontrada")
    return result


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    user: str = Depends(require_auth),
) -> Dict[str, bool]:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden eliminar carpetas")
    if not _folders.delete(folder_id, _owner(user) or user):
        raise HTTPException(status_code=404, detail="Carpeta no encontrada")
    return {"ok": True}


# ── Item endpoints ─────────────────────────────────────────────────────────────

@router.get("", response_model=List[Dict[str, Any]])
async def list_items(
    type: Optional[str] = None,
    user: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    if is_guest(user):
        items = get_session(user).knowledge
        return [i for i in items if not type or i["type"] == type]
    items = _storage.list(_owner(user), type)
    role = get_user_role(user)
    if role not in ("admin",):
        shared_ids = set(_sharing_ts.get_user_shared_resource_ids(user, "knowledge"))
        own_ids = {i["id"] for i in items}
        for kid in (shared_ids - own_ids):
            k = _storage.get(kid)
            if k and (not type or k.get("type") == type):
                k["_shared"] = True
                items.append(k)
    return items


class ItemMove(BaseModel):
    folder_id: Optional[str] = None


@router.patch("/{item_id}")
async def move_item(
    item_id: str,
    body: ItemMove,
    user: str = Depends(require_auth),
) -> Dict[str, bool]:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden mover items")
    if not _storage.move(item_id, body.folder_id, _owner(user)):
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return {"ok": True}


@router.post("/url")
async def add_url(
    request: Request,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    body = await request.json()
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip() or url
    folder_id: Optional[str] = body.get("folder_id") or None
    if not url:
        raise HTTPException(status_code=422, detail="URL requerida")
    try:
        content = fetch_url_text(url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo obtener la URL: {exc}") from exc
    if not content.strip():
        raise HTTPException(status_code=422, detail="No se pudo extraer texto de la URL")
    if is_guest(user):
        item = _guest_item(type="url", title=title, source=url, content=content)
        get_session(user).knowledge.append(item)
        return item
    owner = _owner(user) or "admin"
    return _storage.save(type="url", title=title, source=url, content=content, owner_id=owner, folder_id=folder_id)


@router.post("/document")
async def upload_document(
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    filename = file.filename or "documento"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=422,
            detail=f"Formato no soportado. Formatos permitidos: {', '.join(_ALLOWED_EXTS)}",
        )
    content_bytes = await file.read()
    if len(content_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El fichero supera el límite de 10 MB")
    try:
        content = extract_document_text(content_bytes, filename, file.content_type or "")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not content.strip():
        raise HTTPException(status_code=422, detail="No se pudo extraer texto del documento")
    if is_guest(user):
        item = _guest_item(type="document", title=filename, source=filename, content=content)
        get_session(user).knowledge.append(item)
        return item
    owner = _owner(user) or "admin"
    return _storage.save(
        type="document",
        title=filename,
        source=filename,
        content=content,
        owner_id=owner,
        folder_id=folder_id or None,
    )


@router.delete("/{item_id}")
async def delete_item(
    item_id: str,
    user: str = Depends(require_auth),
) -> Dict[str, bool]:
    if is_guest(user):
        s = get_session(user)
        before = len(s.knowledge)
        s.knowledge = [i for i in s.knowledge if i["id"] != item_id]
        if len(s.knowledge) == before:
            raise HTTPException(status_code=404, detail="Item no encontrado")
        return {"ok": True}
    if not _storage.delete(item_id, _owner(user)):
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return {"ok": True}
