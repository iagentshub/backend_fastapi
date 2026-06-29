"""Rutas de conocimiento: URLs, documentos y carpetas."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.api.routes.auth import WorkspaceContext, require_workspace
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, DB_FILE, SKILLS_DIR

from app.storage.guest import get_session, is_guest
from app.storage.knowledge import FolderStorage, KnowledgeStorage, extract_document_text, fetch_url_text
from app.storage.storage import AgentStorage, SkillStorage
router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_storage = KnowledgeStorage(DB_FILE)
_folders = FolderStorage(DB_FILE)
_agents  = AgentStorage(AGENTS_DIR)
_skills  = SkillStorage(SKILLS_DIR)

_ALLOWED_EXTS = {".txt", ".md", ".pdf"}
_VALID_SECTIONS = {"document", "url", "skill", "agents", "memory"}


async def _owner(user: str, workspace_id: str) -> Optional[str]:
    return None if await get_user_role(user) == "admin" else workspace_id


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
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        return []
    return await _folders.list((await _owner(user, workspace_id)) or workspace_id, section)


@router.post("/folders", response_model=Dict[str, Any])
async def create_folder(
    body: FolderCreate,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden crear carpetas")
    if body.section not in _VALID_SECTIONS:
        raise HTTPException(status_code=422, detail=f"Sección no válida: {body.section}")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío")
    return await _folders.create((await _owner(user, workspace_id)) or workspace_id, body.section, name)


@router.patch("/folders/{folder_id}", response_model=Dict[str, Any])
async def rename_folder(
    folder_id: str,
    body: FolderRename,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden renombrar carpetas")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío")
    result = await _folders.rename(folder_id, (await _owner(user, workspace_id)) or workspace_id, name)
    if result is None:
        raise HTTPException(status_code=404, detail="Carpeta no encontrada")
    return result


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    cascade: bool = False,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, bool]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden eliminar carpetas")
    if cascade:
        folder = await _folders.get(folder_id)
        section = (folder or {}).get("section", "")
        if section == "agents":
            for agent in _agents.list(scope="private"):
                if agent.get("folder_id") == folder_id:
                    try:
                        _agents.delete(agent["id"])
                    except Exception:
                        pass
        elif section == "skill":
            for skill in _skills.list(scope="private"):
                if skill.get("folder_id") == folder_id:
                    try:
                        _skills.delete("private", skill["id"])
                    except Exception:
                        pass
    if not await _folders.delete(folder_id, (await _owner(user, workspace_id)) or workspace_id, cascade=cascade):
        raise HTTPException(status_code=404, detail="Carpeta no encontrada")
    return {"ok": True}


# ── Item endpoints ─────────────────────────────────────────────────────────────

@router.get("", response_model=List[Dict[str, Any]])
async def list_items(
    type: Optional[str] = None,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        items = get_session(user).knowledge
        return [i for i in items if not type or i["type"] == type]
    items = await _storage.list(await _owner(user, workspace_id), type)
    role = await get_user_role(user)
    if role not in ("admin",):
        from app.storage.groups import GroupStorage as _GS

        shared_ids = set(await _GS(DB_FILE).get_user_shared_resource_ids(user, "knowledge", workspace_id))
        own_ids = {i["id"] for i in items}
        extra = []
        for kid in (shared_ids - own_ids):
            k = await _storage.get(kid)
            if k and (not type or k.get("type") == type):
                k["_shared"] = True
                extra.append(k)
        items = items + extra
    return items


class ItemMove(BaseModel):
    folder_id: Optional[str] = None


@router.patch("/{item_id}")
async def move_item(
    item_id: str,
    body: ItemMove,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, bool]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden mover items")
    if not await _storage.move(item_id, body.folder_id, await _owner(user, workspace_id)):
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return {"ok": True}


@router.post("/text")
async def add_text(
    request: Request,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    body = await request.json()
    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "").strip()
    source = str(body.get("source") or title).strip()
    folder_id: Optional[str] = body.get("folder_id") or None
    if not title:
        raise HTTPException(status_code=422, detail="Título requerido")
    if not content:
        raise HTTPException(status_code=422, detail="Contenido requerido")
    if is_guest(user):
        item = _guest_item(type="text", title=title, source=source, content=content)
        get_session(user).knowledge.append(item)
        return item
    owner = await _owner(user, workspace_id) or workspace_id
    return await _storage.save(type="text", title=title, source=source, content=content, owner_id=owner, folder_id=folder_id)


@router.post("/url")
async def add_url(
    request: Request,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
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
    owner = await _owner(user, workspace_id) or workspace_id
    return await _storage.save(type="url", title=title, source=url, content=content, owner_id=owner, folder_id=folder_id)


@router.post("/document")
async def upload_document(
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
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
    owner = await _owner(user, workspace_id) or workspace_id
    return await _storage.save(
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
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, bool]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        s = get_session(user)
        before = len(s.knowledge)
        s.knowledge = [i for i in s.knowledge if i["id"] != item_id]
        if len(s.knowledge) == before:
            raise HTTPException(status_code=404, detail="Item no encontrado")
        return {"ok": True}
    if not await _storage.delete(item_id, await _owner(user, workspace_id)):
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return {"ok": True}
