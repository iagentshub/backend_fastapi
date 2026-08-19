"""`OfficialSourceImporter`: descarga, detecta y valida de una pasada.

Lo detectado aquí no se persiste: se lo lleva `official_source_sync` para
materializarlo como recursos normales.
"""


from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from app.services.official_source_importer._shared import (
    _ALLOWED_LICENSES,
    _MAX_ARCHIVE_BYTES,
    OfficialRepositoryImportError,
)
from app.services.official_source_importer.detection import detect_components
from app.services.official_source_importer.download import (
    _json_request,
    _request,
    _safe_archive_files,
    parse_repository_url,
)
from app.services.official_source_importer.validation import (
    unique_import_notices,
    validate_components,
)
from app.storage.official_source_storage import OfficialSourceStorage


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
        snapshot = await self.inspect_snapshot(
            repository_url,
            tracking_mode=tracking_mode,
            tracking_ref=tracking_ref,
        )
        return self.analyze_snapshot(snapshot)

    async def inspect_snapshot(
        self,
        repository_url: str,
        *,
        tracking_mode: str = "release",
        tracking_ref: str = "",
    ) -> Dict[str, Any]:
        """Descarga fijada a commit y devuelve todos los textos seguros."""
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
        version, sha, archive_url = await self._resolve_version(source)
        raw = await asyncio.to_thread(
            _request,
            archive_url,
            accept="application/zip",
            max_bytes=_MAX_ARCHIVE_BYTES,
            too_large_message="El repositorio comprimido supera 100 MB",
        )
        return {
            "source": {**source, "commit_sha": sha, "resolved_version": version},
            "version": version,
            "commit_sha": sha,
            "files": _safe_archive_files(raw),
        }

    def analyze_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Aplica el detector manual a un snapshot ya descargado."""
        source = dict(snapshot["source"])
        components = detect_components(str(source.get("id") or "draft"), snapshot["files"])
        errors: List[str] = []
        warnings: List[Any] = []
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
            "source": source,
            "version": snapshot["version"],
            "commit_sha": snapshot["commit_sha"],
            "components": components,
            "errors": errors,
            "security_warnings": unique_import_notices(warnings),
        }

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
        return self.analyze_snapshot(
            {
                "source": {**source, "commit_sha": sha, "resolved_version": version},
                "files": files,
                "version": version,
                "commit_sha": sha,
            }
        )

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
