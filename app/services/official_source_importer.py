"""Descarga segura y determinista del contenido de una fuente oficial.

Lo detectado aquí no se persiste: se lo lleva official_source_sync para
materializarlo como recursos normales."""

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
from urllib.parse import quote, urlencode, urlparse

import yaml

from app.models.official_source import COMPONENT_TYPES, PackageComponent
from app.storage.official_source_storage import OfficialSourceStorage
from app.storage.skill_storage import SKILL_LABELS, ensure_origin_label

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
    {
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".txt",
        ".js",
        ".mjs",
        ".ts",
        ".py",
        ".sh",
        ".ps1",
    }
)
_DANGEROUS_PATTERNS = (
    (re.compile(r"\brm\s+-rf\b", re.IGNORECASE), "borrado recursivo"),
    (
        re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sh|bash)\b", re.IGNORECASE),
        "descarga ejecutada por shell",
    ),
    (
        re.compile(r"\b(Invoke-Expression|IEX)\b", re.IGNORECASE),
        "ejecución dinámica de PowerShell",
    ),
    (
        re.compile(r"\b(child_process\.(exec|spawn)|subprocess\.)", re.IGNORECASE),
        "creación de procesos",
    ),
)
_ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {"api.github.com", "codeload.github.com", "gitlab.com"}
)


class OfficialRepositoryImportError(ValueError):
    pass


GitHubImportError = OfficialRepositoryImportError


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


def _frontmatter(text: str) -> Dict[str, Any]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        value = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


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


_ROOT_KINDS = {
    "agents": "agent",
    "skills": "skill",
    "commands": "command",
    "prompts": "prompt",
    "knowledge": "knowledge",
    "documents": "knowledge",
    "memory": "memory",
    "tools": "tool",
    "workflows": "workflow",
    "rules": "rule",
    "hooks": "hook",
    "mcp": "mcp",
    "mcp-configs": "mcp",
}
_PLATFORM_ROOTS = frozenset(
    {
        ".agents",
        ".claude",
        ".codex",
        ".openclaw",
        ".opencode",
        ".cursor",
        ".kiro",
        ".gemini",
    }
)
_IGNORED_ROOTS = frozenset(
    {
        ".git",
        ".github",
        "benchmarks",
        "docs",
        "documentation",
        "evals",
        "examples",
        "node_modules",
        "tests",
        "test",
        "vendor",
        "dist",
    }
)
_KIND_EXTENSIONS = {
    "agent": {".md", ".json", ".yaml", ".yml", ".toml"},
    "skill": {".md"},
    "command": {".md", ".toml"},
    "prompt": {".md", ".txt"},
    "knowledge": {".md", ".txt", ".json", ".yaml", ".yml", ".toml"},
    "memory": {".md", ".txt", ".json", ".yaml", ".yml", ".toml"},
    "tool": {".py", ".sh", ".ps1", ".js", ".mjs", ".ts"},
    "workflow": {".json", ".yaml", ".yml"},
    "rule": {".md", ".txt"},
    "hook": {".json", ".yaml", ".yml", ".py", ".sh", ".ps1", ".js", ".ts"},
    "mcp": {".json", ".yaml", ".yml", ".toml"},
}


def _component_location(
    path: str, *, declared: bool = False
) -> Optional[Tuple[str, int]]:
    """Tipo y prioridad. Las raíces del repositorio ganan a sus adaptadores."""
    pure = PurePosixPath(path)
    lowered = tuple(part.lower() for part in pure.parts)
    if not lowered or lowered[0] in _IGNORED_ROOTS:
        return None
    if declared:
        return "unknown", -100
    root = lowered[0]
    kind = _ROOT_KINDS.get(root)
    priority = 0
    if kind is None and root in _PLATFORM_ROOTS:
        nested = next((part for part in lowered[1:] if part in _ROOT_KINDS), None)
        if nested not in {"agents", "skills", "commands", "prompts"}:
            return None
        kind = _ROOT_KINDS[nested]
        priority = 30
    elif kind is None and root == "plugins" and len(lowered) >= 3:
        nested = lowered[2]
        if nested not in {"agents", "skills", "commands", "prompts"}:
            return None
        kind = _ROOT_KINDS[nested]
        priority = 40
    elif kind is None and root == "src" and len(lowered) >= 2:
        nested = lowered[1]
        if nested not in {"tools", "hooks", "rules", "mcp", "mcp-configs"}:
            return None
        kind = _ROOT_KINDS[nested]
        priority = 20
    if kind is None or pure.suffix.lower() not in _KIND_EXTENSIONS[kind]:
        return None
    if kind == "skill" and pure.name.upper() != "SKILL.MD":
        return None
    if kind in {"agent", "command", "prompt"} and len(pure.parts) > 4:
        return None
    if pure.suffix.lower() == ".toml":
        priority += 5
    return kind, priority


def _string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _component_dependencies(
    meta: Dict[str, Any], declared: Dict[str, Any]
) -> List[str]:
    values = _string_list(meta.get("dependencies"))
    # Las claves de permisos de Claude/Codex también suelen llamarse "tools".
    # Solo un manifiesto iAgentsHub puede convertir esas listas en relaciones.
    for key in ("skills", "knowledge", "prompts", "tools", "memory", "workflows"):
        values.extend(_string_list(declared.get(key)))
    relations = declared.get("relations")
    if isinstance(relations, dict):
        for value in relations.values():
            values.extend(_string_list(value))
    return list(dict.fromkeys(_slug(item.split(":", 1)[-1]) for item in values))


def _manifest_components(files: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    for path in (
        ".iagentshub/manifest.json",
        "iagentshub.json",
        "plugin.json",
        "plugin.yaml",
        "plugin.yml",
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
    ):
        raw = files.get(path)
        if not raw:
            continue
        try:
            manifest = (
                json.loads(raw)
                if PurePosixPath(path).suffix == ".json"
                else yaml.safe_load(raw)
            )
        except (json.JSONDecodeError, yaml.YAMLError):
            continue
        declared = manifest.get("components") if isinstance(manifest, dict) else None
        if not isinstance(declared, list):
            return {}
        return {
            str(item.get("source_path") or ""): item
            for item in declared
            if isinstance(item, dict) and item.get("source_path")
        }
    return {}


def detect_components(source_id: str, files: Dict[str, str]) -> List[PackageComponent]:
    candidates: List[Tuple[int, PackageComponent]] = []
    declared_components = _manifest_components(files)
    canonical_kinds = {
        _ROOT_KINDS[PurePosixPath(path).parts[0].lower()]
        for path in files
        if PurePosixPath(path).parts
        and PurePosixPath(path).parts[0].lower() in _ROOT_KINDS
    }
    if "command" in canonical_kinds:
        canonical_kinds.add("prompt")
    for path, content in sorted(files.items()):
        declared = declared_components.get(path, {})
        declared_type = str(
            declared.get("type") or declared.get("component_type") or ""
        )
        location = _component_location(path, declared=bool(declared))
        if not location:
            continue
        inferred_kind, priority = location
        kind = declared_type if declared_type in COMPONENT_TYPES else inferred_kind
        if kind == "unknown" and not declared_type:
            continue
        if kind == "workflow":
            try:
                definition = yaml.safe_load(content) or {}
            except yaml.YAMLError:
                definition = {}
            if not isinstance(definition, dict) or not {
                "nodes",
                "edges",
            }.issubset(definition):
                continue
        pure = PurePosixPath(path)
        meta = {**_frontmatter(content), **declared}
        inferred = pure.parent.name if pure.name.upper() == "SKILL.MD" else pure.stem
        name = str(
            meta.get("name") or inferred.replace("-", " ").replace("_", " ").title()
        )
        component_id = _slug(str(meta.get("id") or inferred))
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
        language = str(meta.get("language") or meta.get("lang") or "").lower()
        language_label = f"lang_{language.replace('-', '_')}" if language else ""
        labels = ensure_origin_label(_string_list(meta.get("labels")), "official")
        if language_label in SKILL_LABELS:
            labels.append(language_label)
        executable_candidate = kind in {"tool", "hook"}
        executable = kind == "tool"
        blocked = executable_candidate and (
            (kind == "tool" and pure.suffix.lower() not in {".py", ".sh"})
            or any(
                pattern.search(content) and label == "borrado recursivo"
                for pattern, label in _DANGEROUS_PATTERNS
            )
        )
        component = PackageComponent(
            source_id=source_id,
            component_id=component_id,
            component_type=kind,
            name=name,
            description=str(meta.get("description") or ""),
            source_path=path,
            content=content,
            files=companion_files,
            labels=list(dict.fromkeys(labels)),
            dependencies=_component_dependencies(meta, declared),
            content_hash=hashlib.sha256(digest_source.encode()).hexdigest(),
            language=language_label,
            detected_by="native_manifest" if declared else "canonical_directory",
            executable=executable,
            security_blocked=blocked,
            security_review_required=executable_candidate,
        )
        candidates.append((priority, component))

    # Una plataforma puede publicar la misma pieza para varios clientes. La
    # raíz canónica produce un objeto; el resto queda registrado como variante.
    grouped: Dict[Tuple[str, str], List[Tuple[int, PackageComponent]]] = {}
    for candidate in candidates:
        component = candidate[1]
        grouped.setdefault(
            (component.component_type, component.component_id), []
        ).append(candidate)
    components: List[PackageComponent] = []
    used_ids: set[str] = set()
    for (kind, base_id), variants in sorted(grouped.items()):
        variants.sort(key=lambda item: (item[0], item[1].source_path))
        if variants[0][0] >= 30 and kind in canonical_kinds:
            continue
        component = variants[0][1]
        component.variants = [item[1].source_path for item in variants[1:]]
        component_id = base_id
        number = 2
        while component_id in used_ids:
            component_id = f"{base_id}-{number}"
            number += 1
        component.component_id = component_id
        used_ids.add(component_id)
        components.append(component)
    return sorted(components, key=lambda item: (item.component_type, item.component_id))


def validate_components(
    components: List[PackageComponent],
) -> Tuple[List[str], List[str]]:
    """Devuelve errores bloqueantes y avisos de seguridad para la revisión."""
    errors: List[str] = []
    warnings: List[str] = []
    component_ids = {component.component_id for component in components}
    dependencies_by_id = {
        component.component_id: component.dependencies for component in components
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> bool:
        if component_id in visiting:
            return True
        if component_id in visited:
            return False
        visiting.add(component_id)
        cyclic = any(
            dependency in dependencies_by_id and visit(dependency)
            for dependency in dependencies_by_id.get(component_id, [])
        )
        visiting.remove(component_id)
        visited.add(component_id)
        return cyclic

    if any(visit(component_id) for component_id in sorted(component_ids)):
        errors.append("El grafo de dependencias contiene un ciclo")
    markdown_link = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for component in components:
        invalid_labels = [
            label for label in component.labels if label not in SKILL_LABELS
        ]
        if invalid_labels:
            errors.append(
                f"{component.component_id}: etiquetas no válidas ({', '.join(invalid_labels)})"
            )
        missing_dependencies = [
            item for item in component.dependencies if item not in component_ids
        ]
        if missing_dependencies:
            errors.append(
                f"{component.component_id}: dependencias no encontradas "
                f"({', '.join(missing_dependencies)})"
            )
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
                if (
                    not destination
                    or "://" in destination
                    or destination.startswith(("#", "mailto:"))
                ):
                    continue
                resolved = posixpath.normpath(
                    PurePosixPath(path).parent.joinpath(destination).as_posix()
                )
                if (
                    resolved == ".."
                    or resolved.startswith("../")
                    or resolved.startswith("/")
                ):
                    errors.append(
                        f"{component.component_id}: referencia fuera del repositorio ({destination})"
                    )
            for pattern, label in _DANGEROUS_PATTERNS:
                if pattern.search(content):
                    warnings.append(
                        f"{component.component_id}: posible {label} en {path}"
                    )
    return sorted(set(errors)), sorted(set(warnings))


class OfficialSourceImporter:
    """Alta de fuentes y descarga de su contenido. No persiste componentes."""

    def __init__(self, storage: Optional[OfficialSourceStorage] = None) -> None:
        self.storage = storage or OfficialSourceStorage()

    async def import_repository(
        self,
        repository_url: str,
        *,
        tracking_mode: str = "release",
        tracking_ref: str = "",
    ) -> Dict[str, Any]:
        """Compatibilidad: registra la fuente. El flujo nuevo usa inspect."""
        inspected = await self.inspect_repository(
            repository_url,
            tracking_mode=tracking_mode,
            tracking_ref=tracking_ref,
        )
        source = await self.storage.save_source(inspected["source"])
        inspected["source"] = source
        for component in inspected["components"]:
            component.source_id = str(source["id"])
        return inspected

    async def inspect_repository(
        self,
        repository_url: str,
        *,
        tracking_mode: str = "release",
        tracking_ref: str = "",
    ) -> Dict[str, Any]:
        """Inspecciona sin crear fuente ni objetos; primera fase del borrador."""
        parsed = parse_repository_url(repository_url)
        if tracking_mode not in {"release", "branch"}:
            raise OfficialRepositoryImportError(
                "tracking_mode debe ser 'release' o 'branch'"
            )
        metadata = await self._metadata(parsed)
        if metadata.get("private") is True or metadata.get("visibility") not in {
            None,
            "public",
        }:
            raise OfficialRepositoryImportError("Solo se admiten repositorios públicos")
        default_branch = str(metadata.get("default_branch") or "main")
        source = {
            "id": "draft",
            "name": str(metadata.get("name") or parsed["repository_name"]),
            "description": str(metadata.get("description") or ""),
            **parsed,
            "default_branch": default_branch,
            "tracking_mode": tracking_mode,
            "tracking_ref": tracking_ref or default_branch,
            "license": str((metadata.get("license") or {}).get("spdx_id") or ""),
        }
        return await self._fetch_source(source)

    async def fetch(self, source_id: str) -> Dict[str, Any]:
        source = await self.storage.get_source(source_id)
        if not source:
            raise KeyError("source_not_found")
        try:
            result = await self._fetch_source(source)
            await self.storage.mark_sync(
                source_id,
                version=result["version"],
                commit_sha=result["commit_sha"],
            )
            result["source"] = await self.storage.get_source(source_id)
            return result
        except Exception as exc:
            await self.storage.mark_sync(source_id, error=str(exc))
            raise

    async def _fetch_source(self, source: Dict[str, Any]) -> Dict[str, Any]:
        version, sha, archive_url = await self._resolve_version(source)
        raw = await asyncio.to_thread(
            _request,
            archive_url,
            accept="application/zip",
            max_bytes=_MAX_ARCHIVE_BYTES,
            too_large_message="El repositorio comprimido supera 100 MB",
        )
        files = _safe_archive_files(raw)
        source_id = str(source.get("id") or "draft")
        components = detect_components(source_id, files)
        errors: List[str] = []
        warnings: List[str] = []
        if source.get("license") not in _ALLOWED_LICENSES:
            warnings.append(
                "La licencia no está reconocida o no pertenece al catálogo conocido"
            )
        if not components:
            errors.append("No se detectaron componentes compatibles")
        content_errors, security_warnings = validate_components(components)
        errors.extend(content_errors)
        warnings.extend(security_warnings)
        return {
            "source": {**source, "commit_sha": sha, "resolved_version": version},
            "version": version,
            "commit_sha": sha,
            "components": components,
            "errors": errors,
            "security_warnings": sorted(set(warnings)),
        }

    async def _metadata(self, parsed: Dict[str, str]) -> Dict[str, Any]:
        if parsed["provider"] == "github":
            metadata = await _json_request(
                f"https://api.github.com/repos/{parsed['repository_path']}"
            )
            if metadata.get("private") is True:
                raise OfficialRepositoryImportError(
                    "Solo se admiten repositorios públicos"
                )
            return metadata
        project = quote(parsed["repository_path"], safe="")
        return await _json_request(f"https://gitlab.com/api/v4/projects/{project}")

    async def _resolve_version(self, source: Dict[str, Any]) -> Tuple[str, str, str]:
        if source.get("provider", "github") == "gitlab":
            return await self._resolve_gitlab_version(source)
        return await self._resolve_github_version(source)

    async def _resolve_github_version(
        self, source: Dict[str, Any]
    ) -> Tuple[str, str, str]:
        owner = source["repository_owner"]
        repository = source["repository_name"]
        if source["tracking_mode"] == "release":
            try:
                release = await _json_request(
                    f"https://api.github.com/repos/{owner}/{repository}/releases/latest"
                )
                tag = str(release.get("tag_name") or "").strip()
                if tag:
                    ref = await _json_request(
                        f"https://api.github.com/repos/{owner}/{repository}/commits/"
                        f"{quote(tag, safe='')}"
                    )
                    sha = str(ref.get("sha") or "")
                    return (
                        tag,
                        sha,
                        f"https://codeload.github.com/{owner}/{repository}/zip/{sha}",
                    )
            except OfficialRepositoryImportError:
                pass
        branch = str(
            source.get("tracking_ref") or source.get("default_branch") or "main"
        )
        ref = await _json_request(
            f"https://api.github.com/repos/{owner}/{repository}/commits/"
            f"{quote(branch, safe='')}"
        )
        sha = str(ref.get("sha") or "")
        if not sha:
            raise OfficialRepositoryImportError(
                "GitHub no devolvió el commit de la referencia"
            )
        return (
            sha[:12],
            sha,
            f"https://codeload.github.com/{owner}/{repository}/zip/{sha}",
        )

    async def _resolve_gitlab_version(
        self, source: Dict[str, Any]
    ) -> Tuple[str, str, str]:
        project = quote(str(source["repository_path"]), safe="")
        ref_name = str(
            source.get("tracking_ref") or source.get("default_branch") or "main"
        )
        version = ""
        if source["tracking_mode"] == "release":
            try:
                release = await _json_request(
                    f"https://gitlab.com/api/v4/projects/{project}/releases/permalink/latest"
                )
                version = str(release.get("tag_name") or "")
                ref_name = version or ref_name
            except OfficialRepositoryImportError:
                pass
        ref = await _json_request(
            f"https://gitlab.com/api/v4/projects/{project}/repository/commits/"
            f"{quote(ref_name, safe='')}"
        )
        sha = str(ref.get("id") or "")
        if not sha:
            raise OfficialRepositoryImportError(
                "GitLab no devolvió el commit de la referencia"
            )
        query = urlencode({"sha": sha})
        return (
            version or sha[:12],
            sha,
            f"https://gitlab.com/api/v4/projects/{project}/repository/archive.zip?{query}",
        )
