"""Disk-backed progressive staging for local agent directories."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterable

from fastapi import UploadFile

from app.api.routes.auth import GroupContext
from app.config import data as _cfg
from app.config.directory_import import DIRECTORY_IMPORT_MAX_FILES
from app.errors import APIError
from app.middleware.body_limit import configured_max_bytes
from app.utils.generators import generate_id

_TTL_SECONDS = 30 * 60
_CHUNK_BYTES = 1024 * 1024


def _root() -> Path:
    return _cfg.DATA_DIR / "agent_import_upload_sessions"


def _invalid_session() -> APIError:
    return APIError(
        422,
        "invalid_field",
        "La carga del directorio ha caducado; vuelve a seleccionarlo",
        extra={"field": "session_id", "reason": "expired_upload_session"},
    )


def _session_dir(session_id: str) -> Path:
    if len(session_id) != 32 or any(char not in "0123456789abcdef" for char in session_id):
        raise _invalid_session()
    return _root() / session_id


def _load_metadata(session_id: str, ctx: GroupContext) -> tuple[Path, dict]:
    directory = _session_dir(session_id)
    try:
        payload = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise _invalid_session() from None
    if (
        payload.get("user") != ctx.user
        or payload.get("group_id") != ctx.group_id
        or float(payload.get("created_at") or 0) < time.time() - _TTL_SECONDS
    ):
        raise _invalid_session()
    return directory, payload


def _cleanup_expired(root: Path) -> None:
    cutoff = time.time() - _TTL_SECONDS
    for candidate in root.iterdir():
        try:
            if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate)
        except OSError:
            continue


async def create_upload_session(total_files: int, ctx: GroupContext) -> str:
    if total_files < 1 or total_files > DIRECTORY_IMPORT_MAX_FILES:
        raise APIError(
            422,
            "invalid_field",
            "El número de archivos del directorio no es válido",
            extra={
                "field": "total_files",
                "reason": "too_many_files",
                "max_files": DIRECTORY_IMPORT_MAX_FILES,
            },
        )
    session_id = generate_id(32)

    def create() -> None:
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        directory = root / session_id
        directory.mkdir(mode=0o700)
        metadata = {
            "user": ctx.user,
            "group_id": ctx.group_id,
            "created_at": time.time(),
            "total_files": total_files,
        }
        target = directory / "session.json"
        target.write_text(json.dumps(metadata), encoding="utf-8")
        os.chmod(target, 0o600)
        _cleanup_expired(root)

    await asyncio.to_thread(create)
    return session_id


async def store_upload_file(
    session_id: str,
    *,
    index: int,
    relative_path: str,
    upload: UploadFile,
    ctx: GroupContext,
) -> None:
    directory, metadata = await asyncio.to_thread(_load_metadata, session_id, ctx)
    total_files = int(metadata["total_files"])
    if index < 0 or index >= total_files:
        raise APIError(
            422,
            "invalid_field",
            "El índice del archivo no es válido",
            extra={"field": "file_index", "reason": "invalid_file_index"},
        )
    temporary = directory / f".{index:08d}.{generate_id(8)}.tmp"

    def copy_upload() -> int:
        size = 0
        upload.file.seek(0)
        with temporary.open("wb") as output:
            while chunk := upload.file.read(_CHUNK_BYTES):
                size += len(chunk)
                output.write(chunk)
        return size

    try:
        size = await asyncio.to_thread(copy_upload)
        os.chmod(temporary, 0o600)
        data_target = directory / f"{index:08d}.bin"
        os.replace(temporary, data_target)
        sidecar = directory / f".{index:08d}.json.tmp"
        sidecar.write_text(
            json.dumps({"relative_path": relative_path, "size": size}),
            encoding="utf-8",
        )
        os.chmod(sidecar, 0o600)
        os.replace(sidecar, directory / f"{index:08d}.json")
    finally:
        temporary.unlink(missing_ok=True)

    limit = configured_max_bytes()
    if limit > 0:
        staged_total = 0
        for candidate in directory.glob("[0-9]*.json"):
            try:
                staged_total += int(json.loads(candidate.read_text())["size"])
            except (OSError, ValueError, TypeError, KeyError):
                continue
        if staged_total > limit:
            data_target.unlink(missing_ok=True)
            (directory / f"{index:08d}.json").unlink(missing_ok=True)
            raise APIError(
                413,
                "payload_too_large",
                "Payload demasiado grande",
                extra={"limit_bytes": limit},
            )


def _staged_entries(directory: Path, metadata: dict) -> list[tuple[str, Path]]:
    expected = int(metadata["total_files"])
    entries: list[tuple[str, Path]] = []
    staged_total = 0
    for index in range(expected):
        sidecar = directory / f"{index:08d}.json"
        content = directory / f"{index:08d}.bin"
        try:
            relative_path = str(json.loads(sidecar.read_text())["relative_path"])
        except (OSError, ValueError, TypeError, KeyError):
            raise APIError(
                422,
                "invalid_field",
                "Faltan archivos por subir",
                extra={"field": "files", "reason": "incomplete_upload_session"},
            ) from None
        if not content.is_file():
            raise APIError(
                422,
                "invalid_field",
                "Faltan archivos por subir",
                extra={"field": "files", "reason": "incomplete_upload_session"},
            )
        staged_total += int(json.loads(sidecar.read_text())["size"])
        entries.append((relative_path, content))
    limit = configured_max_bytes()
    if limit > 0 and staged_total > limit:
        raise APIError(
            413,
            "payload_too_large",
            "Payload demasiado grande",
            extra={"limit_bytes": limit},
        )
    return entries


@asynccontextmanager
async def claim_staged_uploads(
    session_id: str, ctx: GroupContext
) -> AsyncIterator[Iterable[tuple[str, bytes]]]:
    """Atomically freeze a complete upload while its review plan is built."""

    def claim() -> tuple[Path, Path, dict]:
        directory, metadata = _load_metadata(session_id, ctx)
        claimed = _root() / f".{session_id}.{generate_id(8)}.claimed"
        try:
            os.replace(directory, claimed)
        except OSError:
            raise _invalid_session() from None
        return directory, claimed, metadata

    directory, claimed, metadata = await asyncio.to_thread(claim)
    succeeded = False
    try:
        entries = await asyncio.to_thread(_staged_entries, claimed, metadata)
        yield ((path, source.read_bytes()) for path, source in entries)
        succeeded = True
    finally:
        def release() -> None:
            if succeeded:
                shutil.rmtree(claimed, ignore_errors=True)
            elif claimed.exists() and not directory.exists():
                os.replace(claimed, directory)

        await asyncio.to_thread(release)


async def delete_upload_session(session_id: str, ctx: GroupContext) -> None:
    try:
        directory, _ = await asyncio.to_thread(_load_metadata, session_id, ctx)
    except APIError:
        return
    await asyncio.to_thread(shutil.rmtree, directory, True)
