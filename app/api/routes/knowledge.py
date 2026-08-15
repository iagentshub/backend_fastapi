"""Rutas de conocimiento: URLs y documentos."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR
from app.errors import APIError
from app.models.request_bodies import (
    KnowledgeEditBody,
    KnowledgePackEditBody,
    KnowledgePackManifestBody,
    KnowledgePackUploadSessionBody,
    KnowledgeTextBody,
    KnowledgeUrlBody,
    LabelsBody,
)
from app.pagination.materialized import paginate_materialized
from app.pagination.models import OffsetParams
from app.services.knowledge_listing import list_authenticated_knowledge
from app.storage.agent_storage import AgentStorage
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.guest import get_session, is_guest
from app.storage.knowledge import (
    KnowledgeStorage,
    extract_document_text,
    fetch_url_text,
)
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.skill_storage import (
    SKILL_ASSIGNABLE_LABELS,
    SKILL_LABELS,
    ensure_origin_label,
)
from app.utils.generators import generate_id
from app.utils.origin import assert_resource_writable

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_storage = KnowledgeStorage()
_packs = KnowledgePackStorage()
_agents = AgentStorage(AGENTS_DIR)
_shares = GroupShareStorage()
_groups = GroupStorage()

_IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
}
_PACK_TEXT_EXTS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".adoc",
    ".pdf",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".dart",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".cs",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".sql",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".xml",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".properties",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    *_IMAGE_EXTS,
}
_PACK_TEXT_NAMES = {
    "dockerfile",
    "makefile",
    "rakefile",
    "gemfile",
    "procfile",
    "license",
    "readme",
    "changelog",
    "notice",
    "skill.md",
    "agents.md",
}
_PACK_IGNORED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "build",
    "dist",
    ".dart_tool",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}
_PACK_SECRET_NAMES = {
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "secrets.json",
}
_PACK_MAX_FILES = 500
_PACK_SESSION_MAX_FILES = 5000
_PACK_MAX_FILE_BYTES = 10 * 1024 * 1024
_PACK_MAX_TOTAL_BYTES = 50 * 1024 * 1024
_PACK_SESSION_MAX_TOTAL_BYTES = 500 * 1024 * 1024


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _manifest_file(raw: Dict[str, Any]) -> Dict[str, Any]:
    path = _normalize_pack_path(str(raw.get("relative_path") or ""))
    checksum = str(raw.get("checksum") or "").strip().lower()
    try:
        size = int(raw.get("size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise APIError(
            422,
            "invalid_field",
            "El tamaño declarado no es válido",
            extra={"field": "size_bytes", "path": path},
        ) from exc
    if size < 0 or not _valid_sha256(checksum):
        raise APIError(
            422,
            "invalid_field",
            "El manifiesto contiene metadatos no válidos",
            extra={"field": "checksum", "path": path},
        )
    mime_type = str(raw.get("mime_type") or "").strip().lower()[:255]
    modified_at = raw.get("modified_at")
    return {
        "relative_path": path,
        "kind": _pack_kind(path),
        "mime_type": mime_type,
        "size_bytes": size,
        "checksum": checksum,
        "modified_at": int(modified_at) if modified_at is not None else None,
    }


async def _owner(user: str, group_id: str) -> Optional[str]:
    return None if await get_user_role(user) == "admin" else group_id


def _content_labels(body: Dict[str, Any], *, allow_origin: bool = False) -> List[str]:
    raw = body.get("labels")
    if raw is None:
        return ensure_origin_label(["private"], "community")
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
    allowed_labels = (
        SKILL_LABELS if allow_origin else SKILL_ASSIGNABLE_LABELS | {"community"}
    )
    invalid = [value for value in labels if value not in allowed_labels]
    if invalid:
        raise APIError(
            422,
            "invalid_field",
            "Knowledge contiene labels fuera del catálogo del sistema",
            extra={"field": "labels", "invalid": invalid},
        )
    visibility = [value for value in labels if value in {"public", "private"}]
    if len(visibility) > 1:
        raise APIError(
            422,
            "invalid_field",
            "La visibilidad solo puede ser privada o pública",
            extra={"field": "labels", "invalid": visibility},
        )
    selected_visibility = visibility[0] if visibility else "private"
    editable = [value for value in labels if value not in {"public", "private"}]
    return ensure_origin_label(
        [selected_visibility, *editable], None if allow_origin else "community"
    )


def _edited_labels(raw: Any, existing: Dict[str, Any]) -> List[str]:
    if not isinstance(raw, list):
        raise APIError(
            422,
            "invalid_field",
            "Las labels deben ser una lista del catálogo del sistema",
            extra={"field": "labels"},
        )
    selected = list(
        dict.fromkeys(str(value).strip() for value in raw if str(value).strip())
    )
    origin_labels = {"community", "official"}
    assignable = [value for value in selected if value not in origin_labels]
    invalid = [value for value in assignable if value not in SKILL_ASSIGNABLE_LABELS]
    if invalid:
        raise APIError(
            422,
            "invalid_field",
            "Knowledge contiene labels fuera del catálogo del sistema",
            extra={"field": "labels", "invalid": invalid},
        )
    visibility_labels = [
        value for value in assignable if value in {"private", "public"}
    ]
    if len(visibility_labels) > 1:
        raise APIError(
            422,
            "invalid_field",
            "La visibilidad solo puede ser privada o pública",
            extra={"field": "labels", "invalid": visibility_labels},
        )
    existing_labels = [str(value) for value in existing.get("labels") or []]
    visibility = (
        visibility_labels[0]
        if visibility_labels
        else ("public" if "public" in existing_labels else "private")
    )
    editable = [value for value in assignable if value not in {"private", "public"}]
    origin = "official" if "official" in existing_labels else "community"
    return ensure_origin_label([visibility, *editable], origin)


async def _sync_social_visibility(
    *, resource_type: str, resource_id: str, ctx: GroupContext, is_public: bool
) -> None:
    # Import local para mantener las rutas desacopladas durante el arranque de
    # FastAPI. La publicación sigue usando la misma lógica que Explorar.
    from app.api.routes.social import sync_knowledge_visibility_from_labels

    await sync_knowledge_visibility_from_labels(
        resource_type=resource_type,
        resource_id=resource_id,
        username=ctx.user,
        owner_ids={ctx.user, ctx.group_id},
        is_public=is_public,
    )


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


def _normalize_pack_path(raw: str) -> str:
    value = raw.replace("\\", "/").strip("/")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise APIError(
            422,
            "invalid_field",
            "El pack contiene una ruta no válida",
            extra={"field": "paths", "path": raw},
        )
    if len(path.parts) > 32 or len(value) > 500:
        raise APIError(
            422,
            "invalid_field",
            "Una ruta del pack supera los límites permitidos",
            extra={"field": "paths", "path": raw},
        )
    return path.as_posix()


def _pack_skip_reason(relative_path: str) -> Optional[str]:
    path = PurePosixPath(relative_path)
    lower_parts = {part.lower() for part in path.parts[:-1]}
    name = path.name.lower()
    if lower_parts & _PACK_IGNORED_DIRS:
        return "directorio_ignorado"
    if (
        name in _PACK_SECRET_NAMES
        or (name.startswith(".env.") and name != ".env.example")
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
    ):
        return "posible_secreto"
    return None


def _pack_file_is_extractable(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    name = path.name.lower()
    suffix = path.suffix.lower()
    compound_suffix = "".join(path.suffixes[-2:]).lower()
    return (
        suffix in _PACK_TEXT_EXTS
        or compound_suffix in _PACK_TEXT_EXTS
        or name in _PACK_TEXT_NAMES
        or name.startswith(("readme.", "license.", "changelog."))
    )


def _catalogued_file_content(relative_path: str, mime_type: str, size: int) -> str:
    return (
        f"Archivo catalogado dentro del pack: {relative_path}\n"
        f"Tipo: {mime_type or 'desconocido'}\n"
        f"Tamano: {size} bytes\n"
        "El formato no contiene texto extraible directamente."
    )


def _pack_kind(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name == "skill.md" or "skills" in {part.lower() for part in path.parts[:-1]}:
        return "skill"
    if suffix in {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".dart",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".swift",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".sql",
    } or name in {"dockerfile", "makefile", "rakefile"}:
        return "script"
    if suffix in _IMAGE_EXTS:
        return "image"
    if suffix in {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}:
        return "archive"
    if suffix in {
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".xml",
    }:
        return "config"
    return "document" if _pack_file_is_extractable(relative_path) else "asset"


def _pack_reported_size(raw_sizes: List[Any], index: int, fallback: int) -> int:
    if not raw_sizes:
        return fallback
    try:
        value = int(raw_sizes[index])
    except (IndexError, TypeError, ValueError) as exc:
        raise APIError(
            422,
            "invalid_field",
            "El tamaño de uno de los archivos no es válido",
            extra={"field": "sizes"},
        ) from exc
    if value < 0:
        raise APIError(
            422,
            "invalid_field",
            "El tamaño de uno de los archivos no es válido",
            extra={"field": "sizes"},
        )
    return value


# ── Item endpoints ─────────────────────────────────────────────────────────────


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
    user = ctx.user
    if is_guest(user):
        items = get_session(user).knowledge
        filtered = [i for i in items if not type or i["type"] == type]
        filtered = paginate_materialized(
            filtered, limit=limit, offset=offset, response=response
        )
        return filtered
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
        allow_origin=not is_guest(user) and await get_user_role(user) == "admin",
    )
    if not title:
        raise APIError(
            422, "invalid_field", "Título requerido", extra={"field": "title"}
        )
    if not content:
        raise APIError(
            422, "invalid_field", "Contenido requerido", extra={"field": "content"}
        )
    if is_guest(user):
        labels = ensure_origin_label(
            ["private", *[label for label in labels if label != "public"]],
            "community",
        )
        item = _guest_item(
            type="text", title=title, source=source, content=content, labels=labels
        )
        get_session(user).knowledge.append(item)
        return item
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
        allow_origin=not is_guest(user) and await get_user_role(user) == "admin",
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
    if is_guest(user):
        labels = ensure_origin_label(
            ["private", *[label for label in labels if label != "public"]],
            "community",
        )
        item = _guest_item(
            type="url", title=title, source=url, content=content, labels=labels
        )
        get_session(user).knowledge.append(item)
        return item
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
        allow_origin=not is_guest(user) and await get_user_role(user) == "admin",
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
        except Exception:
            content = ""
    if not content.strip() or "\x00" in content:
        content = _catalogued_file_content(
            filename, file.content_type or "", len(content_bytes)
        )
    if is_guest(user):
        content_labels = ensure_origin_label(
            [
                "private",
                *[label for label in content_labels if label != "public"],
            ],
            "community",
        )
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


# ── Knowledge packs ──────────────────────────────────────────────────────────


@router.get("/packs", response_model=List[Dict[str, Any]])
async def list_packs(
    requested_group_id: Optional[str] = Query(None, alias="group_id"),
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    user = ctx.user
    if is_guest(user):
        return []
    role = await get_user_role(user)
    if requested_group_id is not None:
        if role != "admin" and not await _groups.can_access(requested_group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        ids = await _shares.get_group_shared_resource_ids(
            requested_group_id, "knowledge_pack"
        )
        packs = []
        for pack_id in ids:
            pack = await _packs.get(pack_id, include_items=False)
            if pack:
                pack["_shared"] = True
                pack["_group_id"] = requested_group_id
                packs.append(pack)
    else:
        packs = await _packs.list(ctx.group_id)
        own_ids = {pack["id"] for pack in packs}
        for group in await _groups.list_for_user(user):
            group_id = group["id"]
            for pack_id in await _shares.get_group_shared_resource_ids(
                group_id, "knowledge_pack"
            ):
                if pack_id in own_ids:
                    continue
                pack = await _packs.get(pack_id, include_items=False)
                if pack:
                    pack["_shared"] = True
                    pack["_group_id"] = group_id
                    packs.append(pack)
                    own_ids.add(pack_id)
    return packs


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
    if is_guest(user):
        raise APIError(
            403,
            "forbidden",
            "Los invitados no pueden crear packs de conocimiento",
            extra={"resource": "knowledge_pack"},
        )
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
        {"labels": raw_labels}, allow_origin=await get_user_role(user) == "admin"
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
        total_bytes += size
        if size > _PACK_MAX_FILE_BYTES or total_bytes > _PACK_MAX_TOTAL_BYTES:
            raise APIError(
                413,
                "file_too_large",
                "El directorio supera los límites de importación",
                extra={"max_file_mb": 10, "max_total_mb": 50},
            )
        content = ""
        if source_mode == "reference":
            content = (
                f"Referencia externa del pack: {relative_path}\n"
                f"Tamano: {size} bytes\n"
                "El contenido no se copió a iAgents Hub y no puede ser leído "
                "por el agente."
            )
        elif _pack_file_is_extractable(relative_path):
            try:
                content = await asyncio.to_thread(
                    extract_document_text,
                    raw,
                    relative_path,
                    upload.content_type or "",
                )
            except Exception:
                content = ""
        if not content.strip() or "\x00" in content:
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
            except Exception:
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


@router.post("/packs/{pack_id}/sync-manifest")
async def compare_pack_sync_manifest(
    pack_id: str,
    body: KnowledgePackManifestBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    if is_guest(ctx.user):
        raise APIError(403, "forbidden", "Los invitados no pueden sincronizar packs")
    pack = await _packs.get(pack_id)
    if pack is None:
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    assert_resource_writable(pack, "knowledge_pack")
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este pack")
    manifest = [_manifest_file(item.payload()) for item in body.files]
    paths = [str(item["relative_path"]) for item in manifest]
    if len(paths) != len(set(paths)):
        raise APIError(
            422,
            "invalid_field",
            "El directorio contiene rutas duplicadas",
            extra={"field": "files"},
        )
    if len(manifest) > _PACK_SESSION_MAX_FILES:
        raise APIError(
            413,
            "file_too_large",
            "El directorio contiene demasiados archivos",
            extra={"max_files": _PACK_SESSION_MAX_FILES},
        )
    total_bytes = sum(int(item["size_bytes"]) for item in manifest)
    if (
        any(int(item["size_bytes"]) > _PACK_MAX_FILE_BYTES for item in manifest)
        or total_bytes > _PACK_SESSION_MAX_TOTAL_BYTES
    ):
        raise APIError(
            413,
            "file_too_large",
            "El directorio supera los límites de sincronización",
            extra={"max_file_mb": 10, "max_total_mb": 500},
        )
    existing = {
        str(item["relative_path"]): str(item.get("checksum") or "")
        for item in pack.get("items") or []
    }
    incoming = set(paths)
    source_mode = str(pack.get("source_mode") or "upload")
    changed_paths = [
        path
        for path, item in zip(paths, manifest)
        if existing.get(path) != str(item["checksum"])
    ]
    upload_paths = [] if source_mode == "reference" else changed_paths
    return {
        "upload_paths": upload_paths,
        "unchanged": len(manifest) - len(changed_paths),
        "metadata_only": len(changed_paths) if source_mode == "reference" else 0,
        "removed": len(set(existing) - incoming),
        "total": len(manifest),
    }


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


@router.post("/packs/{pack_id}/sync")
async def sync_pack(
    pack_id: str,
    paths: str = Form("[]"),
    sizes: str = Form("[]"),
    manifest: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    if is_guest(ctx.user):
        raise APIError(403, "forbidden", "Los invitados no pueden sincronizar packs")
    pack = await _packs.get(pack_id, include_items=False)
    if pack is None:
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    assert_resource_writable(pack, "knowledge_pack")
    role = await get_user_role(ctx.user)
    if role != "admin" and pack.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este pack")
    source_mode = str(pack.get("source_mode") or "upload")
    try:
        raw_paths = json.loads(paths)
        raw_sizes = json.loads(sizes)
        raw_manifest = json.loads(manifest) if manifest else None
    except (TypeError, ValueError) as exc:
        raise APIError(
            422,
            "invalid_field",
            "Los metadatos de sincronización no son válidos",
            extra={"field": "paths"},
        ) from exc
    if raw_manifest is not None:
        if not isinstance(raw_manifest, list):
            raise APIError(
                422,
                "invalid_field",
                "El manifiesto de sincronización no es válido",
                extra={"field": "manifest"},
            )
        manifest_items = [_manifest_file(dict(item)) for item in raw_manifest]
        manifest_paths = [str(item["relative_path"]) for item in manifest_items]
        if len(manifest_paths) != len(set(manifest_paths)):
            raise APIError(
                422,
                "invalid_field",
                "El directorio contiene rutas duplicadas",
                extra={"field": "manifest"},
            )
    else:
        manifest_items = []
    if (
        not isinstance(raw_paths, list)
        or len(raw_paths) != len(files)
        or not isinstance(raw_sizes, list)
        or (raw_sizes and len(raw_sizes) != len(files))
    ):
        raise APIError(
            422,
            "invalid_field",
            "Cada archivo debe incluir ruta y tamaño",
            extra={"field": "paths"},
        )
    if len(files) > _PACK_SESSION_MAX_FILES:
        raise APIError(
            413,
            "file_too_large",
            "El directorio contiene demasiados archivos",
            extra={"max_files": _PACK_SESSION_MAX_FILES},
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
    total_bytes = 0
    manifest_by_path = {str(item["relative_path"]): item for item in manifest_items}
    existing_checksums = {
        str(item["relative_path"]): str(item.get("checksum") or "")
        for item in (await _packs.get(pack_id) or {}).get("items", [])
    }
    if manifest_items:
        required_paths = (
            set()
            if source_mode == "reference"
            else {
                path
                for path, item in manifest_by_path.items()
                if existing_checksums.get(path) != str(item["checksum"])
            }
        )
        if set(normalized_paths) != required_paths:
            raise APIError(
                422,
                "invalid_field",
                "Faltan archivos modificados o se enviaron archivos innecesarios",
                extra={"field": "files", "required_paths": sorted(required_paths)},
            )
    for index, (upload, relative_path) in enumerate(zip(files, normalized_paths)):
        if _pack_skip_reason(relative_path):
            continue
        raw = await upload.read()
        declared = manifest_by_path.get(relative_path)
        size = (
            int(declared["size_bytes"])
            if declared is not None
            else _pack_reported_size(raw_sizes, index, len(raw))
            if source_mode == "reference"
            else len(raw)
        )
        total_bytes += size
        if declared is not None and source_mode != "reference" and size != len(raw):
            raise APIError(
                422,
                "invalid_field",
                "El archivo recibido no coincide con el tamaño declarado",
                extra={"field": "size_bytes", "path": relative_path},
            )
        if size > _PACK_MAX_FILE_BYTES or total_bytes > _PACK_MAX_TOTAL_BYTES:
            raise APIError(
                413,
                "file_too_large",
                "El directorio supera los límites de sincronización",
                extra={"max_file_mb": 10, "max_total_mb": 50},
            )
        mime_type = str((declared or {}).get("mime_type") or upload.content_type or "")
        if source_mode == "reference":
            content = (
                f"Referencia externa del pack: {relative_path}\n"
                f"Tamano: {size} bytes\n"
                "El contenido no se copió a iAgents Hub."
            )
            checksum = str((declared or {}).get("checksum") or "")
        else:
            checksum = hashlib.sha256(raw).hexdigest()
            if declared is not None and checksum != str(declared["checksum"]):
                raise APIError(
                    422,
                    "invalid_field",
                    "El archivo recibido no coincide con su checksum",
                    extra={"field": "checksum", "path": relative_path},
                )
            content = ""
            if _pack_file_is_extractable(relative_path):
                try:
                    content = await asyncio.to_thread(
                        extract_document_text,
                        raw,
                        relative_path,
                        mime_type,
                    )
                except Exception:
                    content = ""
            if not content.strip() or "\x00" in content:
                content = _catalogued_file_content(relative_path, mime_type, size)
        accepted.append(
            {
                "relative_path": relative_path,
                "kind": _pack_kind(relative_path),
                "mime_type": mime_type,
                "size_bytes": size,
                "checksum": checksum,
                "content": content,
            }
        )
    if manifest_items:
        accepted_by_path = {str(item["relative_path"]): item for item in accepted}
        accepted = [
            {**item, **accepted_by_path.get(str(item["relative_path"]), {})}
            for item in manifest_items
        ]
        if source_mode == "reference":
            for item in accepted:
                item["content"] = (
                    f"Referencia externa del pack: {item['relative_path']}\n"
                    f"Tamano: {item['size_bytes']} bytes\n"
                    "El contenido no se copió a iAgents Hub."
                )
    if not accepted and not manifest_items:
        raise APIError(
            422,
            "document_text_extraction_failed",
            "El directorio no contiene archivos compatibles",
        )
    owner = None if role == "admin" else str(pack["owner_id"])
    changes = await _packs.replace_items(pack_id, owner, accepted)
    if changes is None:
        raise APIError(
            404, "not_found", "Pack no encontrado", extra={"resource": "knowledge_pack"}
        )
    await _sync_social_visibility(
        resource_type="knowledge_pack",
        resource_id=pack_id,
        ctx=ctx,
        is_public="public" in (pack.get("labels") or []),
    )
    updated = await _packs.get(pack_id) or pack
    updated["sync"] = changes
    return updated


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
        _edited_labels(body.labels, pack)
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
    labels = _edited_labels(body.labels, pack)
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


@router.put("/{item_id}")
async def update_item(
    item_id: str,
    body: KnowledgeEditBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    if is_guest(ctx.user):
        raise APIError(403, "forbidden", "Los invitados no pueden editar conocimiento")
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
        _edited_labels(body.labels, item)
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
    if is_guest(ctx.user):
        raise APIError(403, "forbidden", "Los invitados no pueden editar etiquetas")
    item = await _storage.get(item_id)
    if item is None:
        raise APIError(
            404, "not_found", "Item no encontrado", extra={"resource": "item"}
        )
    assert_resource_writable(item, "knowledge")
    role = await get_user_role(ctx.user)
    if role != "admin" and item.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este conocimiento")
    labels = _edited_labels(body.labels, item)
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
    if is_guest(user):
        s = get_session(user)
        before = len(s.knowledge)
        s.knowledge = [i for i in s.knowledge if i["id"] != item_id]
        if len(s.knowledge) == before:
            raise APIError(
                404, "not_found", "Item no encontrado", extra={"resource": "item"}
            )
        return {"ok": True}
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
