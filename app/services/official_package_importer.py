"""Importación segura y determinista de paquetes oficiales desde GitHub."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import posixpath
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from app.models.official_package import EXPORT_TARGETS, PackageComponent
from app.storage.official_package_storage import OfficialPackageStorage

_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
_MAX_UNPACKED_BYTES = 500 * 1024 * 1024
_MAX_IMPORTED_TEXT_BYTES = 60 * 1024 * 1024
_MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
_MAX_FILES = 4000
_ALLOWED_LICENSES = frozenset(
    {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0"}
)
_TEXT_EXTENSIONS = frozenset(
    {".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".js", ".mjs", ".ts", ".py", ".sh", ".ps1"}
)
_DANGEROUS_PATTERNS = (
    (re.compile(r"\brm\s+-rf\b", re.IGNORECASE), "borrado recursivo"),
    (re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sh|bash)\b", re.IGNORECASE), "descarga ejecutada por shell"),
    (re.compile(r"\b(Invoke-Expression|IEX)\b", re.IGNORECASE), "ejecución dinámica de PowerShell"),
    (re.compile(r"\b(child_process\.(exec|spawn)|subprocess\.)", re.IGNORECASE), "creación de procesos"),
)


class GitHubImportError(ValueError):
    pass


def parse_github_repository(repository_url: str) -> Tuple[str, str, str]:
    parsed = urlparse(repository_url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise GitHubImportError("Solo se admiten repositorios https://github.com")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise GitHubImportError("La URL debe apuntar a la raíz de un repositorio GitHub")
    owner, repository = parts
    repository = repository.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+", repository
    ):
        raise GitHubImportError("Propietario o repositorio no válido")
    canonical = f"https://github.com/{owner}/{repository}"
    return owner, repository, canonical


def _request(
    url: str,
    *,
    accept: str = "application/vnd.github+json",
    max_bytes: int = _MAX_JSON_BYTES,
    too_large_message: str = "La respuesta de GitHub supera 2 MB",
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "iAgentsHub-official-package-importer/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = int(response.headers.get("Content-Length") or 0)
            if length > max_bytes:
                raise GitHubImportError(too_large_message)
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GitHubImportError("Repositorio o referencia no encontrados") from exc
        raise GitHubImportError(f"GitHub respondió con HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GitHubImportError("No se pudo conectar con GitHub") from exc
    if len(data) > max_bytes:
        raise GitHubImportError(too_large_message)
    return data


async def _json_request(url: str) -> Dict[str, Any]:
    raw = await asyncio.to_thread(_request, url)
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubImportError("GitHub devolvió una respuesta no válida") from exc
    if not isinstance(value, dict):
        raise GitHubImportError("GitHub devolvió una respuesta inesperada")
    return value


def _frontmatter(text: str) -> Dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    result: Dict[str, str] = {}
    for line in parts[1].splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"name", "description"}:
            result[key.strip()] = value.strip().strip('"\'')
    return result


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


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "component"


def _component_kind(path: str) -> Optional[str]:
    pure = PurePosixPath(path)
    lowered = tuple(part.lower() for part in pure.parts)
    if pure.name.upper() == "SKILL.MD":
        return "skill"
    if "agents" in lowered and pure.suffix.lower() == ".md":
        return "agent"
    if "commands" in lowered and pure.suffix.lower() == ".md":
        return "command"
    if "rules" in lowered and pure.suffix.lower() == ".md":
        return "rule"
    if "hooks" in lowered and pure.suffix.lower() in {".json", ".js", ".ts", ".py", ".sh", ".ps1"}:
        return "hook"
    if ("mcp" in lowered or "mcp-configs" in lowered) and pure.suffix.lower() == ".json":
        return "mcp"
    if "tools" in lowered and pure.suffix.lower() in {".md", ".json", ".yaml", ".yml"}:
        return "tool"
    return None


def detect_components(
    package_id: str, version: str, files: Dict[str, str]
) -> List[PackageComponent]:
    components: List[PackageComponent] = []
    used_ids: set[str] = set()
    for path, content in sorted(files.items()):
        kind = _component_kind(path)
        if not kind:
            continue
        pure = PurePosixPath(path)
        meta = _frontmatter(content)
        inferred = pure.parent.name if pure.name.upper() == "SKILL.MD" else pure.stem
        name = meta.get("name") or inferred.replace("-", " ").replace("_", " ").title()
        base_id = _slug(meta.get("name") or inferred)
        component_id = base_id
        number = 2
        while component_id in used_ids:
            component_id = f"{base_id}-{number}"
            number += 1
        used_ids.add(component_id)
        companion_files: Dict[str, str] = {}
        if kind == "skill":
            prefix = pure.parent.as_posix().rstrip("/") + "/"
            companion_files = {
                candidate[len(prefix) :]: body
                for candidate, body in files.items()
                if candidate.startswith(prefix) and candidate != path
            }
        digest_source = content + "".join(
            key + companion_files[key] for key in sorted(companion_files)
        )
        components.append(
            PackageComponent(
                package_id=package_id,
                version=version,
                component_id=component_id,
                component_type=kind,
                name=name,
                description=meta.get("description", ""),
                source_path=path,
                content=content,
                files=companion_files,
                targets=sorted(EXPORT_TARGETS),
                content_hash=hashlib.sha256(digest_source.encode()).hexdigest(),
            )
        )
    return components


def validate_components(
    components: List[PackageComponent],
) -> Tuple[List[str], List[str]]:
    """Devuelve errores bloqueantes y avisos de seguridad para la revisión."""
    errors: List[str] = []
    warnings: List[str] = []
    markdown_link = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for component in components:
        component_root = PurePosixPath(component.source_path).parent
        texts = {component.source_path: component.content}
        texts.update(
            {
                component_root.joinpath(relative).as_posix(): content
                for relative, content in component.files.items()
            }
        )
        for path, content in texts.items():
            for match in markdown_link.finditer(content):
                destination = match.group(1).strip().split("#", 1)[0]
                if not destination or "://" in destination or destination.startswith(("#", "mailto:")):
                    continue
                resolved = posixpath.normpath(
                    PurePosixPath(path).parent.joinpath(destination).as_posix()
                )
                if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
                    errors.append(
                        f"{component.component_id}: referencia fuera del repositorio ({destination})"
                    )
            for pattern, label in _DANGEROUS_PATTERNS:
                if pattern.search(content):
                    warnings.append(f"{component.component_id}: posible {label} en {path}")
    return sorted(set(errors)), sorted(set(warnings))


class OfficialPackageImporter:
    def __init__(self, storage: Optional[OfficialPackageStorage] = None) -> None:
        self.storage = storage or OfficialPackageStorage()

    async def import_repository(
        self,
        repository_url: str,
        *,
        tracking_mode: str = "release",
        tracking_ref: str = "main",
    ) -> Dict[str, Any]:
        owner, repository, canonical = parse_github_repository(repository_url)
        if tracking_mode not in {"release", "branch"}:
            raise GitHubImportError("tracking_mode debe ser 'release' o 'branch'")
        metadata = await _json_request(f"https://api.github.com/repos/{owner}/{repository}")
        license_id = str((metadata.get("license") or {}).get("spdx_id") or "")
        package = await self.storage.save_package(
            {
                "name": str(metadata.get("name") or repository),
                "description": str(metadata.get("description") or ""),
                "repository_url": canonical,
                "repository_owner": owner,
                "repository_name": repository,
                "tracking_mode": tracking_mode,
                "tracking_ref": tracking_ref or str(metadata.get("default_branch") or "main"),
                "license": license_id,
            }
        )
        return await self.sync(str(package["id"]))

    async def sync(self, package_id: str) -> Dict[str, Any]:
        package = await self.storage.get_package(package_id)
        if not package:
            raise KeyError("package_not_found")
        try:
            version, sha, archive_url = await self._resolve_version(package)
            existing = await self.storage.get_version(package_id, version)
            if (
                existing
                and existing.get("commit_sha") == sha
                and existing.get("status") != "draft"
            ):
                await self.storage.mark_sync(package_id)
                return {"changed": False, "package": package, "version": existing}
            # Una release de GitHub puede volver a apuntar a otro commit. No
            # mutamos la versión ya revisada: creamos otro candidato auditable.
            if existing and existing.get("commit_sha") != sha:
                version = f"{version}+{sha[:8]}"
            raw = await asyncio.to_thread(
                _request,
                archive_url,
                accept="application/vnd.github+json",
                max_bytes=_MAX_ARCHIVE_BYTES,
                too_large_message="El repositorio comprimido supera 100 MB",
            )
            files = _safe_archive_files(raw)
            components = detect_components(package_id, version, files)
            errors: List[str] = []
            if package.get("license") not in _ALLOWED_LICENSES:
                errors.append("La licencia no está reconocida o no pertenece al catálogo permitido")
            if not components:
                errors.append("No se detectaron componentes compatibles")
            content_errors, security_warnings = validate_components(components)
            errors.extend(content_errors)
            manifest = {
                "schema_version": 1,
                "source": package["repository_url"],
                "version": version,
                "commit_sha": sha,
                "components": [item.as_dict() for item in components],
                "security_warnings": security_warnings,
            }
            saved = await self.storage.save_version(
                package_id, version, sha, manifest, components, errors
            )
            await self.storage.mark_sync(package_id)
            return {"changed": True, "package": package, "version": saved}
        except Exception as exc:
            await self.storage.mark_sync(package_id, error=str(exc))
            raise

    async def sync_all(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for package in await self.storage.list_packages():
            try:
                results.append(await self.sync(str(package["id"])))
            except Exception as exc:
                results.append({"changed": False, "package": package, "error": str(exc)})
        return results

    async def _resolve_version(self, package: Dict[str, Any]) -> Tuple[str, str, str]:
        owner = package["repository_owner"]
        repository = package["repository_name"]
        if package["tracking_mode"] == "release":
            try:
                release = await _json_request(
                    f"https://api.github.com/repos/{owner}/{repository}/releases/latest"
                )
                tag = str(release.get("tag_name") or "").strip()
                if tag:
                    ref = await _json_request(
                        f"https://api.github.com/repos/{owner}/{repository}/commits/{tag}"
                    )
                    sha = str(ref.get("sha") or "")
                    return tag, sha, f"https://api.github.com/repos/{owner}/{repository}/zipball/{tag}"
            except GitHubImportError:
                pass
        branch = str(package.get("tracking_ref") or "main")
        ref = await _json_request(
            f"https://api.github.com/repos/{owner}/{repository}/commits/{branch}"
        )
        sha = str(ref.get("sha") or "")
        if not sha:
            raise GitHubImportError("GitHub no devolvió el commit de la referencia")
        return sha[:12], sha, f"https://api.github.com/repos/{owner}/{repository}/zipball/{sha}"
