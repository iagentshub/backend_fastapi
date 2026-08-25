"""Rutas de Tools: definiciones compartibles y artefactos para ejecución local.

El servidor cataloga, valida y distribuye; nunca ejecuta código. Python y Shell
usan fuente textual. C++ conserva la fuente cuando existe y requiere además un
artefacto nativo identificado por sistema, arquitectura y SHA-256.

Estas rutas estuvieron cerradas a los invitados mientras su sesión era un dict
en memoria: GuestSession no contemplaba tools y escribir la rama habría sido
duplicar cada handler. Hoy el invitado es un usuario efímero y las tools son
parte de su espacio personal como el resto. Lo único que sigue cerrado es
publicar (`assert_can_publish`), igual que en skills y prompts.
Ver docs/adr/012-el-invitado-es-un-usuario-efimero.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.auth.user_lookup import get_user_by_id
from app.config.tool_runtimes import (
    TOOL_RUNTIMES,
    TOOL_TARGET_ARCHITECTURES,
    TOOL_TARGET_OSES,
)
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter, principal_key
from app.models.request_bodies import CatalogResourcePayload
from app.pagination.models import OffsetParams
from app.services.publishing import assert_can_publish
from app.services.scoped_resource_listing import list_authenticated_scoped_resources
from app.services.tool_policy import (
    TOOL_SECURITY_LABELS,
    assert_tool_consumable,
    assert_tool_distributable,
)
from app.storage.db import open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.skill_storage import (
    SKILL_ASSIGNABLE_LABELS,
    SKILL_LABELS,
    ensure_origin_label,
)
from app.storage.tool_storage import ToolStorage
from app.utils import flog
from app.utils.origin import assert_resource_writable, compute_origin_type

router = APIRouter(prefix="/api/tools", tags=["tools"])

_storage = ToolStorage()
_shares = GroupShareStorage()
_groups = GroupStorage()
_versions = ResourceVersionStorage()

_VALID_SCOPES = {"public", "private", "all"}

# Una codificación base64 grande crea presión de memoria aunque el cuerpo HTTP
# ya esté acotado por el límite global. Se procesa una por worker; las demás
# esperan sin crear otra copia del fichero en memoria.
_binary_encoding_slot = asyncio.Semaphore(1)
_binary_transfer_limiter = RateLimiter(
    calls=20,
    window=60,
    key_func=principal_key,
    shared=True,
    name="tool-binary-transfer",
    ip_calls=60,
)
_BINARY_CHUNK_BYTES = 256 * 1024
_UNSAFE_FILENAME = re.compile(r'[\x00-\x1f\x7f"\\;]')


def _safe_binary_filename(raw: str | None) -> str:
    """Nombre persistible y seguro para UI y cabeceras HTTP."""
    name = Path(raw or "tool_binary").name
    name = _UNSAFE_FILENAME.sub("_", name).strip(" .")[:255]
    return name or "tool_binary"


def _binary_content_disposition(filename: str) -> str:
    """Content-Disposition sin inyección y con Unicode RFC 5987."""
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "tool_binary"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


async def _read_uploaded_binary(file: UploadFile) -> tuple[bytearray, int, str]:
    """Read once into a DB-ready buffer; avoids base64 and a second full copy."""
    async with _binary_encoding_slot:
        chunks = bytearray()
        size = 0
        digest = hashlib.sha256()
        while chunk := await file.read(_BINARY_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
            chunks.extend(chunk)
        return chunks, size, digest.hexdigest()


def _binary_chunks(binary: bytes):
    for offset in range(0, len(binary), _BINARY_CHUNK_BYTES):
        yield binary[offset : offset + _BINARY_CHUNK_BYTES]


def _native_binary_target(binary: bytes | bytearray) -> tuple[str, str] | None:
    """Recognize supported 64-bit ELF, PE and Mach-O artifacts."""
    if len(binary) >= 20 and binary[:4] == b"\x7fELF" and binary[4] == 2:
        byteorder = "little" if binary[5] == 1 else "big"
        machine = int.from_bytes(binary[18:20], byteorder)
        arch = {62: "x64", 183: "arm64"}.get(machine)
        return ("linux", arch) if arch else None
    if len(binary) >= 64 and binary[:2] == b"MZ":
        pe_offset = int.from_bytes(binary[60:64], "little")
        if (
            len(binary) >= pe_offset + 6
            and binary[pe_offset : pe_offset + 4] == b"PE\0\0"
        ):
            machine = int.from_bytes(binary[pe_offset + 4 : pe_offset + 6], "little")
            arch = {0x8664: "x64", 0xAA64: "arm64"}.get(machine)
            return ("windows", arch) if arch else None
    if len(binary) >= 8 and binary[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}:
        byteorder = "little" if binary[:4] == b"\xcf\xfa\xed\xfe" else "big"
        cpu = int.from_bytes(binary[4:8], byteorder)
        arch = {0x01000007: "x64", 0x0100000C: "arm64"}.get(cpu)
        return ("macos", arch) if arch else None
    return None


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise APIError(
            400, "invalid_field", "Scope no válido", extra={"field": "scope"}
        )


def _mark_origin(tl: Dict[str, Any], user: str, group_id: str) -> None:
    """Solo marca origin_type cuando es tuyo o enlazado — deja sin marcar las
    tools públicas de otros usuarios que aparecen en el listado (no son tuyas
    ni un enlace, no hay badge que mostrar)."""
    if tl.get("_shared") or tl.get("owner_id") in (user, group_id):
        tl["origin_type"] = compute_origin_type(tl)


async def _assert_read_access(
    tool_id: str, tl: Dict[str, Any], ctx: GroupContext
) -> None:
    """Lanza 403 si el usuario no puede leer una tool privada (mismo patrón
    que get_skill/get_prompt): propietario, group activo, admin, o compartida
    con alguno de los grupos del usuario."""
    user_group = ctx.group_id
    owner_id = tl.get("owner_id")
    if owner_id in (ctx.user, user_group):
        return
    if await get_user_role(ctx.user) == "admin":
        return
    user_groups = await _groups.list_for_user(ctx.user)
    if user_groups:
        group_ids = [g["id"] for g in user_groups]
        for gid in group_ids:
            shared = await _shares.get_group_shared_resource_ids(gid, "tool")
            if tool_id in shared:
                tl["_shared"] = True
                return
    raise APIError(403, "forbidden", "No tienes acceso a esta tool")


@router.get("")
async def list_tools(
    scope: str = "all",
    owner_scope: str = "group",
    group_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    _check_scope(scope)
    return await list_authenticated_scoped_resources(
        _storage,
        ctx=ctx,
        scope=scope,
        page=OffsetParams(limit=limit, offset=offset),
        response=response,
        requested_group_id=group_id,
        mark_origin=_mark_origin,
    )


@router.get("/{scope}/{tool_id}")
async def get_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user = ctx.user
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )

    role = await get_user_role(user)
    if scope == "private":
        await _assert_read_access(tool_id, tl, ctx)
    elif role != "admin" and tl.get("owner_id") not in {user, ctx.group_id}:
        # Ser pública no adelanta la revisión: el propietario y Admin pueden
        # inspeccionarla, pero nadie más recibe código retenido o incompleto.
        assert_tool_distributable(tl)

    _mark_origin(tl, user, ctx.group_id)
    return tl


@router.post("/{scope}")
async def save_tool(
    scope: str,
    body: CatalogResourcePayload,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if scope == "public":
        assert_can_publish(user)
    payload = body.payload()
    role = await get_user_role(user)
    allowed_labels = (
        SKILL_LABELS
        if role == "admin"
        else SKILL_ASSIGNABLE_LABELS | {"community", "fork"} | TOOL_SECURITY_LABELS
    )
    raw_labels = payload.get("labels")
    if raw_labels is not None:
        if not isinstance(raw_labels, list):
            raise APIError(
                422,
                "invalid_field",
                "Las labels deben ser una lista del catálogo del sistema",
                extra={"field": "labels"},
            )
        labels = list(
            dict.fromkeys(
                str(label).strip() for label in raw_labels if str(label).strip()
            )
        )
        invalid_labels = [label for label in labels if label not in allowed_labels]
        if invalid_labels:
            raise APIError(
                422,
                "invalid_field",
                "La tool contiene labels que no existen en el catálogo del sistema",
                extra={"field": "labels", "invalid": invalid_labels},
            )
        visibility = [label for label in labels if label in {"private", "public"}]
        environments = [
            label
            for label in labels
            if label in {"production", "staging", "development", "test"}
        ]
        if len(visibility) > 1 or len(environments) > 1:
            raise APIError(
                422,
                "invalid_field",
                "La tool contiene labels mutuamente excluyentes",
                extra={"field": "labels"},
            )
        if not visibility:
            labels.insert(0, scope if scope in {"private", "public"} else "private")
        payload["labels"] = labels
    if role != "admin":
        payload["labels"] = ensure_origin_label(
            [str(label) for label in (payload.get("labels") or [scope]) if label],
            "community",
        )

    language = str(payload.get("language") or "").strip()
    if language not in TOOL_RUNTIMES:
        raise APIError(
            422,
            "invalid_field",
            "Lenguaje de tool no válido",
            extra={"field": "language"},
        )
    payload["language"] = language
    target_os = str(payload.get("target_os") or "").strip()
    target_arch = str(payload.get("target_arch") or "").strip()
    if target_os and target_os not in TOOL_TARGET_OSES:
        raise APIError(
            422,
            "invalid_field",
            "Sistema objetivo no válido",
            extra={"field": "target_os"},
        )
    if target_arch and target_arch not in TOOL_TARGET_ARCHITECTURES:
        raise APIError(
            422,
            "invalid_field",
            "Arquitectura objetivo no válida",
            extra={"field": "target_arch"},
        )
    tool_id_in_payload = payload.get("id")
    existing = None
    if tool_id_in_payload:
        existing = await _storage.get_any(
            tool_id_in_payload,
            owner_id=None if role == "admin" else group_id,
        )
        if existing:
            assert_resource_writable(existing, "tool")
            if existing.get("owner_id") is None:
                raise APIError(
                    403,
                    "public_tool_readonly",
                    "Las tools públicas de sistema son de solo lectura",
                )
        if (
            role != "admin"
            and not existing
            and await _storage.get_any(tool_id_in_payload)
        ):
            raise APIError(
                403,
                "forbidden",
                "No tienes permiso para editar esta tool",
                extra={"resource": "tool"},
            )
    if tool_id_in_payload and not existing:
        # Un id entrante solo es válido para editar una fila existente;
        # en altas el id lo genera siempre el servidor.
        payload.pop("id", None)
    if role != "admin":
        # review/quarantine are security state, not user-editable decoration.
        # Preserve an existing hold and add review whenever executable content
        # changes. Owners may still inspect/use their own Tool; distribution is
        # blocked centrally until an admin clears the existing label.
        requested_labels = [
            str(label)
            for label in (payload.get("labels") or [scope])
            if label and str(label) not in TOOL_SECURITY_LABELS
        ]
        protected_labels = [
            str(label)
            for label in ((existing or {}).get("labels") or [])
            if str(label) in TOOL_SECURITY_LABELS
        ]
        implementation_changed = existing is None or any(
            str(payload.get(field) or "").strip()
            != str((existing or {}).get(field) or "").strip()
            for field in ("language", "content")
        )
        if implementation_changed and "review" not in protected_labels:
            protected_labels.append("review")
        payload["labels"] = list(dict.fromkeys([*requested_labels, *protected_labels]))
    was_update = existing is not None
    save_owner_id = (
        str(existing.get("owner_id") or group_id)
        if role == "admin" and existing
        else group_id
    )
    try:
        async with open_db() as conn:
            async with conn.transaction(immediate=True):
                saved = await _storage.save(
                    scope, payload, owner_id=save_owner_id, conn=conn
                )
                await _versions.create(
                    "tool",
                    str(saved["id"]),
                    save_owner_id,
                    saved,
                    user,
                    reason="save",
                    conn=conn,
                )
        action = "actualizada" if was_update else "creada"
        flog.info(
            f"Tool {action}: {saved['id']} {saved.get('name', '')!r}", username=user
        )
        return saved
    except ValueError as e:
        raise APIError(422, "invalid_tool_data", str(e)) from e


@router.delete("/{scope}/{tool_id}")
async def delete_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    if scope == "public":
        assert_can_publish(user)
    # Ownership check before delete
    tl = await _storage.get_any(tool_id)
    if tl:
        assert_resource_writable(tl, "tool")
    role = await get_user_role(user)
    if tl and tl.get("scope") == "public" and tl.get("owner_id") is None:
        raise APIError(
            403,
            "public_tool_readonly",
            "Las tools públicas de sistema son de solo lectura",
        )
    if tl and role != "admin" and tl.get("owner_id") != group_id:
        raise APIError(403, "forbidden", "No tienes permiso para eliminar esta tool")
    try:
        delete_owner = (
            tl.get("owner_id")
            if scope == "public" and tl
            else (None if role == "admin" else group_id)
        )
        if not await _storage.delete(scope, tool_id, owner_id=delete_owner):
            raise APIError(
                404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
            )
    except ValueError as e:
        raise APIError(403, "public_tool_readonly", str(e)) from e
    flog.info(f"Tool borrada: {tool_id} {(tl or {}).get('name', '')!r}", username=user)
    return {"ok": True}


async def _set_tool_active(
    scope: str, tool_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    _check_scope(scope)
    tool = await _storage.get(scope, tool_id)
    if tool is None:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    assert_resource_writable(tool, "tool")
    role = await get_user_role(ctx.user)
    if role != "admin" and tool.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "No tienes permiso para modificar esta tool")
    owner = None if role == "admin" else str(tool["owner_id"])
    if not await _storage.set_active(tool_id, owner, active):
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    return await _storage.get(scope, tool_id) or {"id": tool_id, "is_active": active}


@router.post("/{scope}/{tool_id}/activate")
async def activate_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_tool_active(scope, tool_id, True, ctx)


@router.post("/{scope}/{tool_id}/deactivate")
async def deactivate_tool(
    scope: str, tool_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_tool_active(scope, tool_id, False, ctx)


# ── Binario (solo tools cpp) — subida/descarga en dos pasos ─────────────────
# Mismo patrón que auth.py::upload_avatar / users.py::get_avatar: JSON de
# metadatos primero (POST /api/tools/{scope}), binario aparte.


@router.post("/{scope}/{tool_id}/binary")
async def upload_tool_binary(
    scope: str,
    tool_id: str,
    file: UploadFile = File(...),
    _: None = Depends(_binary_transfer_limiter),
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    assert_resource_writable(tl, "tool")
    role = await get_user_role(user)
    owner_id = tl.get("owner_id")
    if role != "admin" and owner_id not in (user, group_id):
        raise APIError(403, "forbidden", "No tienes permiso para modificar esta tool")
    if tl.get("language") != "cpp":
        raise APIError(
            422,
            "tool_language_not_binary",
            "Solo las tools de lenguaje 'cpp' admiten binario",
            extra={"field": "language"},
        )
    declared_target = (
        str(tl.get("target_os") or ""),
        str(tl.get("target_arch") or ""),
    )
    if not all(declared_target):
        raise APIError(
            422,
            "invalid_field",
            "Indica sistema operativo y arquitectura antes de subir el binario",
            extra={"field": "target_os"},
        )

    binary_data, size, sha256 = await _read_uploaded_binary(file)
    if size == 0:
        raise APIError(400, "tool_binary_empty", "El binario no puede estar vacío")
    detected_target = _native_binary_target(binary_data)
    if detected_target is None or detected_target != declared_target:
        raise APIError(
            422,
            "invalid_field",
            "El formato o arquitectura del binario no coincide con lo declarado",
            extra={
                "field": "binary",
                "declared": list(declared_target),
                "detected": list(detected_target) if detected_target else None,
            },
        )

    # Sin allowlist de extensión (a diferencia del avatar): un binario ELF en
    # Linux normalmente no tiene extensión. Solo se sanea el nombre.
    filename = _safe_binary_filename(file.filename)
    save_owner = owner_id if role == "admin" else group_id
    account = await get_user_by_id(user)
    uploaded_by = str((account or {}).get("username") or user)
    async with open_db() as conn:
        async with conn.transaction(immediate=True):
            ok = await _storage.save_binary(
                tool_id,
                save_owner,
                binary_data,
                filename,
                size,
                sha256=sha256,
                uploaded_by=uploaded_by,
                conn=conn,
            )
            if not ok:
                raise APIError(
                    404,
                    "not_found",
                    "Tool no encontrada",
                    extra={"resource": "tool"},
                )
            updated = await _storage.get(
                scope, tool_id, owner_id=str(save_owner), conn=conn
            )
            if updated:
                await _versions.create(
                    "tool",
                    tool_id,
                    str(save_owner),
                    updated,
                    user,
                    reason="binary_upload",
                    conn=conn,
                )
    flog.info(
        f"Binario subido a tool {tool_id}: {filename} ({size} bytes; sha256={sha256})",
        username=user,
    )
    return {
        "ok": True,
        "binary_filename": filename,
        "binary_size": size,
        "binary_sha256": sha256,
        "labels": (updated or {}).get("labels")
        or sorted(set(tl.get("labels") or []) | {"review"}),
    }


@router.get("/{scope}/{tool_id}/binary")
async def download_tool_binary(
    scope: str,
    tool_id: str,
    request: Request,
    _: None = Depends(_binary_transfer_limiter),
    ctx: GroupContext = Depends(require_group_session),
) -> Response:
    _check_scope(scope)
    tl = await _storage.get(scope, tool_id)
    if not tl:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    role = await get_user_role(ctx.user)
    if scope == "private":
        await _assert_read_access(tool_id, tl, ctx)
    if not tl.get("binary_filename"):
        raise APIError(
            404,
            "not_found",
            "Esta tool no tiene binario",
            extra={"resource": "tool"},
        )
    assert_tool_consumable(
        tl,
        user_id=ctx.user,
        group_id=ctx.group_id,
        is_admin=role == "admin",
    )

    metadata_sha256 = str(tl.get("binary_sha256") or "")
    if (
        metadata_sha256
        and request.headers.get("if-none-match") == f'"{metadata_sha256}"'
    ):
        return Response(status_code=304, headers={"ETag": f'"{metadata_sha256}"'})

    binary = await _storage.get_binary(scope, tool_id)
    if not binary:
        raise APIError(
            404, "not_found", "Esta tool no tiene binario", extra={"resource": "tool"}
        )
    filename = _safe_binary_filename(binary.get("binary_filename"))
    sha256 = str(binary.get("binary_sha256") or "")
    headers = {
        "Content-Disposition": _binary_content_disposition(filename),
        "Content-Length": str(int(binary.get("binary_size") or 0)),
    }
    if sha256:
        headers["ETag"] = f'"{sha256}"'
    return StreamingResponse(
        _binary_chunks(bytes(binary["binary_data"])),
        media_type="application/octet-stream",
        headers=headers,
    )
