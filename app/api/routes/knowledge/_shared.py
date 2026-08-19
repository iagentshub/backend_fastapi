"""Piezas comunes a los dos dominios de conocimiento: items y packs.

Los almacenes, el catálogo de labels y la clasificación de ficheros por
extensión los usan ambos lados —`upload_document` cataloga un fichero suelto
con los mismos criterios con los que `upload_pack` cataloga cada entrada de un
pack—, así que viven aquí y no en uno de los dos.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import (
    KnowledgeStorage,
)
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.skill_storage import (
    SKILL_ASSIGNABLE_LABELS,
    SKILL_LABELS,
    ensure_origin_label,
)

_storage = KnowledgeStorage()

_packs = KnowledgePackStorage()

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
    from app.services.publication_cascade import sync_knowledge_visibility_from_labels

    await sync_knowledge_visibility_from_labels(
        resource_type=resource_type,
        resource_id=resource_id,
        username=ctx.user,
        owner_ids={ctx.user, ctx.group_id},
        is_public=is_public,
    )

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

def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)

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
