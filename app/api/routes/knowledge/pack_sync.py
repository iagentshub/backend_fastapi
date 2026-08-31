"""Sincronización de un pack contra el manifiesto del cliente.

El cliente manda checksum y tamaño de cada fichero; el servidor contesta qué
falta subir. Es lo que evita re-subir un pack entero para cambiar un fichero.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from fastapi import Depends, File, Form, UploadFile

from app.api.routes.auth import GroupContext, require_group_session
from app.api.routes.knowledge._router import router
from app.api.routes.knowledge._shared import (
    _PACK_SESSION_MAX_FILES,
    _PACK_SESSION_MAX_TOTAL_BYTES,
    _catalogued_file_content,
    _extract_document,
    _extraction_item_fields,
    _normalize_pack_path,
    _pack_file_is_extractable,
    _pack_kind,
    _pack_reported_size,
    _pack_skip_reason,
    _packs,
    _sync_social_visibility,
    _valid_sha256,
)
from app.auth.auth import get_user_role
from app.errors import APIError
from app.models.request_bodies import (
    KnowledgePackManifestBody,
)
from app.utils.origin import assert_resource_writable


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

@router.post("/packs/{pack_id}/sync-manifest")
async def compare_pack_sync_manifest(
    pack_id: str,
    body: KnowledgePackManifestBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
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
    # El techo por fichero lo pone `max_request_bytes` desde el panel; el que se
    # queda aquí es el acumulado de la sesión entera, que reparte el directorio
    # en varias peticiones y por eso el middleware no puede verlo.
    # Ver docs/adr/011-un-solo-limite-de-tamano-y-lo-pone-el-admin.md
    total_bytes = sum(int(item["size_bytes"]) for item in manifest)
    if total_bytes > _PACK_SESSION_MAX_TOTAL_BYTES:
        raise APIError(
            413,
            "file_too_large",
            "El directorio supera los límites de sincronización",
            extra={"limit_bytes": _PACK_SESSION_MAX_TOTAL_BYTES},
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

@router.post("/packs/{pack_id}/sync")
async def sync_pack(
    pack_id: str,
    paths: str = Form("[]"),
    sizes: str = Form("[]"),
    manifest: str = Form(""),
    files: List[UploadFile] = File(default=[]),
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
        if total_bytes > _PACK_SESSION_MAX_TOTAL_BYTES:
            raise APIError(
                413,
                "file_too_large",
                "El directorio supera los límites de sincronización",
                extra={"limit_bytes": _PACK_SESSION_MAX_TOTAL_BYTES},
            )
        mime_type = str((declared or {}).get("mime_type") or upload.content_type or "")
        extraction = None
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
                extraction = await _extract_document(raw, relative_path, mime_type)
                content = extraction.text
            if not content.strip() or "\x00" in content:
                extraction = None
                content = _catalogued_file_content(relative_path, mime_type, size)
        accepted.append(
            {
                "relative_path": relative_path,
                "kind": _pack_kind(relative_path),
                "mime_type": mime_type,
                "size_bytes": size,
                "checksum": checksum,
                "content": content,
                **_extraction_item_fields(extraction),
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
