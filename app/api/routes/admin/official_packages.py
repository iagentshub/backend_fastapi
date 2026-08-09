"""Administración de fuentes, sincronizaciones y revisiones oficiales."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from fastapi import Depends
from pydantic import BaseModel, Field

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.errors import APIError
from app.services.official_package_exporter import (
    OfficialPackageCopier,
    select_package_components,
)
from app.services.official_package_importer import (
    GitHubImportError,
    OfficialPackageImporter,
    parse_github_repository,
)
from app.storage.official_package_storage import OfficialPackageStorage

_storage = OfficialPackageStorage()
_importer = OfficialPackageImporter(_storage)


class ImportPackageBody(BaseModel):
    repository_url: str = Field(min_length=1, max_length=500)
    tracking_mode: Literal["release", "branch"] = "release"
    tracking_ref: str = Field(default="main", min_length=1, max_length=200)


class UpdatePackageBody(ImportPackageBody):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    license: str = Field(default="", max_length=100)


class PublishVersionBody(BaseModel):
    component_ids: List[str] = Field(default_factory=list, max_length=500)


def _not_found() -> APIError:
    return APIError(
        404, "not_found", "Paquete o versión no encontrados", extra={"resource": "official_package"}
    )


@admin_router.get("/official-packages")
async def admin_list_official_packages(
    _: str = Depends(require_admin),
) -> List[Dict[str, Any]]:
    packages = await _storage.list_packages()
    for package in packages:
        summaries = await _storage.list_versions(str(package["id"]))
        package["versions"] = [
            await _storage.get_version(str(package["id"]), str(version["version"]))
            for version in summaries
        ]
    return packages


@admin_router.post("/official-packages/import")
async def admin_import_official_package(
    body: ImportPackageBody, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    try:
        return await _importer.import_repository(
            body.repository_url,
            tracking_mode=body.tracking_mode,
            tracking_ref=body.tracking_ref,
        )
    except GitHubImportError as exc:
        raise APIError(422, "official_package_import_failed", str(exc)) from exc


@admin_router.put("/official-packages/{package_id}")
async def admin_update_official_package(
    package_id: str,
    body: UpdatePackageBody,
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        owner, repository, canonical_url = parse_github_repository(
            body.repository_url
        )
        updated = await _storage.update_package(
            package_id,
            {
                **body.model_dump(),
                "repository_url": canonical_url,
                "repository_owner": owner,
                "repository_name": repository,
            },
        )
    except GitHubImportError as exc:
        raise APIError(
            422,
            "invalid_field",
            str(exc),
            extra={"field": "repository_url"},
        ) from exc
    except ValueError as exc:
        raise APIError(
            409,
            "already_exists",
            "Ya existe una fuente oficial para este repositorio",
            extra={"resource": "official_package"},
        ) from exc
    if not updated:
        raise _not_found()
    return updated


@admin_router.post("/official-packages/{package_id}/sync")
async def admin_sync_official_package(
    package_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    try:
        return await _importer.sync(package_id)
    except KeyError as exc:
        raise _not_found() from exc
    except GitHubImportError as exc:
        raise APIError(422, "official_package_sync_failed", str(exc)) from exc


@admin_router.delete("/official-packages/{package_id}")
async def admin_delete_official_package(
    package_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    if not await _storage.get_package(package_id):
        raise _not_found()
    retired_links = await OfficialPackageCopier(_storage).retire_links(package_id)
    if not await _storage.delete_package(package_id):
        raise _not_found()
    return {"ok": True, "retired_links": retired_links}


@admin_router.get("/official-packages/{package_id}/versions/{version}/diff")
async def admin_official_package_diff(
    package_id: str, version: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    package = await _storage.get_package(package_id)
    candidate = await _storage.get_version(package_id, version)
    if not package or not candidate:
        raise _not_found()
    published = None
    if package.get("published_version"):
        published = await _storage.get_version(
            package_id, str(package["published_version"])
        )
    old = {
        item["component_id"]: item for item in (published or {}).get("components", [])
    }
    new = {item["component_id"]: item for item in candidate.get("components", [])}
    return {
        "package_id": package_id,
        "from_version": (published or {}).get("version"),
        "to_version": version,
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(
            key for key in set(old) & set(new)
            if old[key]["content_hash"] != new[key]["content_hash"]
        ),
        "unchanged": sorted(
            key for key in set(old) & set(new)
            if old[key]["content_hash"] == new[key]["content_hash"]
        ),
        "validation_errors": candidate["validation_errors"],
        "security_warnings": candidate.get("manifest", {}).get("security_warnings", []),
    }


@admin_router.post("/official-packages/{package_id}/versions/{version}/publish")
async def admin_publish_official_package(
    package_id: str,
    version: str,
    body: PublishVersionBody | None = None,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        package = await _storage.get_package(package_id)
        if not package:
            raise KeyError("package_not_found")
        previous_component_ids: set[str] = set()
        if package.get("published_version"):
            previous = await _storage.get_version(
                package_id, str(package["published_version"])
            )
            previous_component_ids = {
                str(component["component_id"])
                for component in (previous or {}).get("components", [])
            }
        if body and body.component_ids:
            candidate = await _storage.get_version(package_id, version)
            if not candidate:
                raise KeyError("version_not_found")
            selected = select_package_components(
                candidate.get("components") or [], body.component_ids
            )
            await _storage.retain_version_components(
                package_id,
                version,
                [str(component["component_id"]) for component in selected],
            )
        published = await _storage.review_version(
            package_id, version, publish=True, reviewer=admin
        )
        published_component_ids = {
            str(component["component_id"])
            for component in published.get("components", [])
        }
        removed_component_ids = previous_component_ids - published_component_ids
        published["retired_links"] = await OfficialPackageCopier(
            _storage
        ).retire_links(package_id, removed_component_ids)
        return published
    except KeyError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise APIError(409, "official_package_validation_failed", str(exc)) from exc


@admin_router.post("/official-packages/{package_id}/versions/{version}/reject")
async def admin_reject_official_package(
    package_id: str, version: str, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    try:
        return await _storage.review_version(
            package_id, version, publish=False, reviewer=admin
        )
    except KeyError as exc:
        raise _not_found() from exc
