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
from app.services.directory_file_rules import (
    KNOWLEDGE_IGNORED_DIRECTORY_NAMES,
    KNOWLEDGE_SECRET_FILE_NAMES,
    InvalidDirectoryPath,
    directory_skip_reason,
    normalize_relative_path,
)
from app.services.document_executor import run_document_blocking
from app.services.publishing import assert_can_publish
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import (
    ExtractedDocument,
    KnowledgeStorage,
    extract_document,
)
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.skill_storage import (
    SKILL_ASSIGNABLE_LABELS,
    SKILL_LABELS,
    ensure_origin_label,
)
from app.utils import flog

_storage = KnowledgeStorage()

_packs = KnowledgePackStorage()

_shares = GroupShareStorage()

_groups = GroupStorage()


def _extraction_item_fields(extraction: Optional[ExtractedDocument]) -> Dict[str, Any]:
    """Los metadatos de recorte, con la forma que espera el dict de un pack.

    Sin extracción no hay nada que se haya quedado fuera: el contenido llega de
    una referencia o de la ficha catalogada, y ahí lo guardado es todo lo que
    hay.
    """
    if extraction is None or not extraction.truncated:
        return {}
    return {
        "source_char_count": extraction.source_chars,
        "content_truncated": True,
        "truncation_reason": extraction.reason,
    }


async def _extract_document(raw: bytes, path: str, mime: str) -> ExtractedDocument:
    """Extrae el texto de un fichero subido, sin comerse lo que no quepa.

    Los cuatro sitios que importan ficheros llamaban directamente a
    `asyncio.to_thread(extract_document_text, ...)`, con dos consecuencias que
    esto cierra. El pool por defecto de asyncio es donde también corre bcrypt,
    así que unas cuantas subidas grandes a la vez frenaban los logins sin que
    nada lo dijera. Y el texto llegaba ya recortado, sin forma de saber que
    faltaba nada: aquí queda en el log y viaja en el `ExtractedDocument` hasta
    la ficha.
    """
    try:
        extracted = await run_document_blocking(extract_document, raw, path, mime)
    except Exception as exc:  # noqa: BLE001
        # Ancho a propósito: extract_document envuelve parsers de terceros
        # (PDF, ofimática, OCR) y un fichero raro no puede tumbar la subida. Lo
        # que no puede es no dejar rastro: sin esto el usuario sube un PDF,
        # recibe la ficha catalogada en vez del texto y no hay forma de saber
        # por qué.
        flog.warning(f"[knowledge] Extracción fallida de {path} ({mime}): {exc}")
        return ExtractedDocument(text="")
    if extracted.truncated:
        flog.warning(
            f"[knowledge] {path} ({mime}) entró recortado por "
            f"{extracted.reason}: {len(extracted.text)} de ~"
            f"{extracted.source_chars} caracteres"
        )
    return extracted

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

_PACK_MAX_FILES = 500

_PACK_SESSION_MAX_FILES = 5000

# Lo que pesa un archivo o un pack en una sola petición lo decide
# `max_request_bytes` desde el panel; aquí había además un techo de 10 MB por
# archivo y otro de 50 MB por pack, más bajos y no configurables, que rechazaban
# lo que el panel decía aceptar. El que queda es el acumulado de una sesión de
# subida, que reparte el directorio en muchas peticiones y por eso ningún
# middleware puede contarlo.
# Ver docs/adr/011-un-solo-limite-de-tamano-y-lo-pone-el-admin.md
#
# Es **defensa del proceso**, no política de producto, y por eso sigue siendo
# una constante y no un ajuste del panel: acota lo que una sola sesión puede
# acumular antes de que nadie confirme nada. Un límite que protege la memoria no
# debe poder subirlo el administrador, que es justo quien lo tocaría el día que
# algo no cabe. Lo que sí es política —cuánto pesa una petición— lo decide
# `max_request_bytes`.
_PACK_SESSION_MAX_TOTAL_BYTES = 500 * 1024 * 1024


async def _owner(user: str, group_id: str) -> Optional[str]:
    return None if await get_user_role(user) == "admin" else group_id


def _content_labels(
    body: Dict[str, Any], *, user: str, allow_origin: bool = False
) -> List[str]:
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
    normalized = ensure_origin_label(
        [selected_visibility, *editable], None if allow_origin else "community"
    )
    if "public" in normalized:
        assert_can_publish(user)
    return normalized


def _edited_labels(raw: Any, existing: Dict[str, Any], *, user: str) -> List[str]:
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
    normalized = ensure_origin_label([visibility, *editable], origin)
    if "public" in normalized:
        assert_can_publish(user)
    return normalized


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
    reason = directory_skip_reason(
        relative_path,
        ignored_directory_names=KNOWLEDGE_IGNORED_DIRECTORY_NAMES,
        secret_file_names=KNOWLEDGE_SECRET_FILE_NAMES,
    )
    return {
        "ignored_directory": "directorio_ignorado",
        "possible_secret": "posible_secreto",
    }.get(reason)


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
    try:
        return normalize_relative_path(raw)
    except InvalidDirectoryPath as exc:
        message = (
            "Una ruta del pack supera los límites permitidos"
            if exc.reason == "path_too_long"
            else "El pack contiene una ruta no válida"
        )
        raise APIError(
            422,
            "invalid_field",
            message,
            extra={"field": "paths", "path": raw},
        ) from None


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
