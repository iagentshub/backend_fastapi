"""Traer el contenido de un repositorio sin fiarse de él.

`_SafeRedirectHandler` existe porque seguir una redirección a ciegas convierte
la descarga en un SSRF: el host permitido se comprueba en cada salto, no solo
en la URL que nos dieron.
"""


from __future__ import annotations

import asyncio
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import PurePosixPath
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from app.services.official_source_importer._shared import (
    _ALLOWED_DOWNLOAD_HOSTS,
    _MAX_FILES,
    _MAX_IMPORTED_TEXT_BYTES,
    _MAX_JSON_BYTES,
    _MAX_TEXT_FILE_BYTES,
    _MAX_UNPACKED_BYTES,
    _TEXT_EXTENSIONS,
    GitHubImportError,
    OfficialRepositoryImportError,
)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if (urlparse(newurl).hostname or "").lower() not in _ALLOWED_DOWNLOAD_HOSTS:
            raise OfficialRepositoryImportError(
                "La descarga intentó redirigir a un host no permitido"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def parse_repository_url(repository_url: str) -> Dict[str, str]:
    """Normaliza una raíz pública GitHub/GitLab, incluidos grupos anidados."""
    parsed = urlparse(repository_url.strip())
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or hostname not in {
        "github.com",
        "www.github.com",
        "gitlab.com",
        "www.gitlab.com",
    }:
        raise OfficialRepositoryImportError(
            "Solo se admiten repositorios públicos HTTPS de GitHub y GitLab"
        )
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise OfficialRepositoryImportError("La URL del repositorio no es válida")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    minimum = 2
    if len(parts) < minimum or (hostname.endswith("github.com") and len(parts) != 2):
        raise OfficialRepositoryImportError(
            "La URL debe apuntar a la raíz de un repositorio"
        )
    parts[-1] = parts[-1].removesuffix(".git")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise OfficialRepositoryImportError("La ruta del proyecto no es válida")
    provider = "gitlab" if hostname.endswith("gitlab.com") else "github"
    project_path = "/".join(parts)
    canonical = f"https://{provider}.com/{project_path}"
    return {
        "provider": provider,
        "repository_path": project_path,
        "repository_owner": "/".join(parts[:-1]),
        "repository_name": parts[-1],
        "repository_url": canonical,
    }

def parse_github_repository(repository_url: str) -> Tuple[str, str, str]:
    parsed = parse_repository_url(repository_url)
    if parsed["provider"] != "github":
        raise OfficialRepositoryImportError("La URL no pertenece a GitHub")
    return (
        parsed["repository_owner"],
        parsed["repository_name"],
        parsed["repository_url"],
    )

def _request(
    url: str,
    *,
    accept: str = "application/vnd.github+json",
    max_bytes: int = _MAX_JSON_BYTES,
    too_large_message: str = "La respuesta de GitHub supera 2 MB",
) -> bytes:
    hostname = (urlparse(url).hostname or "").lower()
    if hostname not in _ALLOWED_DOWNLOAD_HOSTS:
        raise OfficialRepositoryImportError("Host de descarga no permitido")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "iAgentsHub-official-package-importer/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        with opener.open(request, timeout=30) as response:
            final_host = (urlparse(response.geturl()).hostname or "").lower()
            if final_host not in _ALLOWED_DOWNLOAD_HOSTS:
                raise OfficialRepositoryImportError(
                    "La descarga redirigió a un host no permitido"
                )
            length = int(response.headers.get("Content-Length") or 0)
            if length > max_bytes:
                raise OfficialRepositoryImportError(too_large_message)
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise OfficialRepositoryImportError(
                "Repositorio o referencia no encontrados"
            ) from exc
        raise OfficialRepositoryImportError(
            f"El proveedor respondió con HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise OfficialRepositoryImportError(
            "No se pudo conectar con el proveedor"
        ) from exc
    if len(data) > max_bytes:
        raise OfficialRepositoryImportError(too_large_message)
    return data

async def _json_request(url: str) -> Dict[str, Any]:
    raw = await asyncio.to_thread(_request, url)
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OfficialRepositoryImportError(
            "El proveedor devolvió una respuesta no válida"
        ) from exc
    if not isinstance(value, dict):
        raise OfficialRepositoryImportError(
            "El proveedor devolvió una respuesta inesperada"
        )
    return value

def _safe_archive_files(raw: bytes) -> Dict[str, str]:
    files: Dict[str, str] = {}
    unpacked = 0
    imported_text = 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise GitHubImportError("El archivo descargado no es un ZIP válido") from exc
    infos = [item for item in archive.infolist() if not item.is_dir()]
    if len(infos) > _MAX_FILES:
        raise GitHubImportError("El repositorio contiene más de 4.000 archivos")
    for info in infos:
        # GitHub añade una carpeta raíz hash; se elimina sin extraer nada a disco.
        parts = PurePosixPath(info.filename).parts
        if len(parts) < 2:
            continue
        relative = PurePosixPath(*parts[1:])
        if relative.is_absolute() or ".." in relative.parts:
            raise GitHubImportError("El repositorio contiene una ruta insegura")
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise GitHubImportError("El repositorio contiene enlaces simbólicos")
        unpacked += info.file_size
        if unpacked > _MAX_UNPACKED_BYTES:
            raise GitHubImportError("El repositorio descomprimido supera 500 MB")
        suffix = relative.suffix.lower()
        if suffix not in _TEXT_EXTENSIONS or info.file_size > _MAX_TEXT_FILE_BYTES:
            continue
        imported_text += info.file_size
        if imported_text > _MAX_IMPORTED_TEXT_BYTES:
            raise GitHubImportError("El contenido de texto importable supera 60 MB")
        try:
            files[relative.as_posix()] = archive.read(info).decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files
