"""Rutas de conocimiento: URLs y documentos adjuntables a agentes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.storage.guest import is_guest
from app.storage.knowledge import KnowledgeStorage, extract_document_text, fetch_url_text

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_storage = KnowledgeStorage(DB_FILE)

_ALLOWED_MIMES = {
    "text/plain",
    "text/markdown",
    "application/pdf",
    "application/octet-stream",
}
_ALLOWED_EXTS = {".txt", ".md", ".pdf"}


def _owner(user: str) -> Optional[str]:
    return None if get_user_role(user) == "admin" else user


def _require_auth_no_guest(user: str) -> None:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden gestionar conocimiento")


@router.get("", response_model=List[Dict[str, Any]])
async def list_items(
    type: Optional[str] = None,
    user: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    if is_guest(user):
        return []
    return _storage.list(_owner(user), type)


@router.post("/url")
async def add_url(
    request: Request,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    _require_auth_no_guest(user)
    body = await request.json()
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip() or url
    if not url:
        raise HTTPException(status_code=422, detail="URL requerida")
    try:
        content = fetch_url_text(url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo obtener la URL: {exc}") from exc
    if not content.strip():
        raise HTTPException(status_code=422, detail="No se pudo extraer texto de la URL")
    owner = _owner(user) or "admin"
    return _storage.save(type="url", title=title, source=url, content=content, owner_id=owner)


@router.post("/document")
async def upload_document(
    file: UploadFile = File(...),
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    _require_auth_no_guest(user)
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
    owner = _owner(user) or "admin"
    return _storage.save(
        type="document",
        title=filename,
        source=filename,
        content=content,
        owner_id=owner,
    )


@router.delete("/{item_id}")
async def delete_item(
    item_id: str,
    user: str = Depends(require_auth),
) -> Dict[str, bool]:
    _require_auth_no_guest(user)
    if not _storage.delete(item_id, _owner(user)):
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return {"ok": True}
