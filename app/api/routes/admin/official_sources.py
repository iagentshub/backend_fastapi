"""Fuentes oficiales: alta, sincronización y marcado manual.

Lo que una fuente trae se materializa como recurso normal del admin (ver
services/official_source_sync), así que aquí no hay catálogo paralelo que
revisar ni publicar: solo la fuente y qué componentes suyos se quedan.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends
from pydantic import BaseModel, Field

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.errors import APIError
from app.models.official_source import INTERNAL_SOURCE_ID, MATERIALIZABLE_TYPES
from app.services.official_source_importer import (
    GitHubImportError,
    OfficialSourceImporter,
    parse_github_repository,
)
from app.services.official_source_sync import OfficialSourceMaterializer
from app.storage.official_source_storage import (
    OFFICIAL_RESOURCE_TABLES,
    OfficialSourceStorage,
)

_storage = OfficialSourceStorage()
_importer = OfficialSourceImporter(_storage)
_materializer = OfficialSourceMaterializer(_storage)


class ImportSourceBody(BaseModel):
    repository_url: str = Field(min_length=1, max_length=500)
    tracking_mode: Literal["release", "branch"] = "release"
    tracking_ref: str = Field(default="main", min_length=1, max_length=200)


class UpdateSourceBody(ImportSourceBody):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    license: str = Field(default="", max_length=100)


class SyncSourceBody(BaseModel):
    """``component_ids`` ausente = solo mirar qué trae la fuente.

    Con lista, esa es exactamente la selección que queda materializada: lo que
    no aparezca se borra.
    """

    component_ids: Optional[List[str]] = Field(default=None, max_length=500)


class MarkOfficialBody(BaseModel):
    official: bool = True


def _not_found() -> APIError:
    return APIError(
        404,
        "not_found",
        "Fuente oficial no encontrada",
        extra={"resource": "official_source"},
    )


async def _fetch_payload(
    source_id: str, body: Optional[SyncSourceBody], admin: str
) -> Dict[str, Any]:
    """Descarga la fuente y, si hay selección, la aplica."""
    fetched = await _importer.fetch(source_id)
    source = fetched["source"]
    components = fetched["components"]
    materialized = {
        str(item["component_id"]): item
        for item in await _storage.list_resources(source_id)
    }
    applied: Optional[Dict[str, Any]] = None
    if body is not None and body.component_ids is not None:
        applied = await _materializer.materialize(
            source, components, body.component_ids, owner_id=admin
        )
        materialized = {
            str(item["component_id"]): item
            for item in await _storage.list_resources(source_id)
        }
    return {
        "source": source,
        "version": fetched["version"],
        "components": [
            {
                **component.as_dict(),
                "materializable": component.component_type in MATERIALIZABLE_TYPES,
            }
            for component in components
        ],
        # Lo que ya está en la aplicación: el panel lo usa para preseleccionar.
        "selected": sorted(materialized),
        "errors": fetched["errors"],
        "security_warnings": fetched["security_warnings"],
        "applied": applied,
    }


@admin_router.get("/official-sources")
async def admin_list_official_sources(
    _: str = Depends(require_admin),
) -> List[Dict[str, Any]]:
    sources = await _storage.list_sources()
    for source in sources:
        source["resources"] = await _storage.list_resources(str(source["id"]))
    return sources


@admin_router.post("/official-sources/import")
async def admin_import_official_source(
    body: ImportSourceBody, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    try:
        fetched = await _importer.import_repository(
            body.repository_url,
            tracking_mode=body.tracking_mode,
            tracking_ref=body.tracking_ref,
        )
    except GitHubImportError as exc:
        raise APIError(422, "official_source_import_failed", str(exc)) from exc
    source_id = str(fetched["source"]["id"])
    return await _fetch_payload(source_id, None, admin)


@admin_router.put("/official-sources/{source_id}")
async def admin_update_official_source(
    source_id: str,
    body: UpdateSourceBody,
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        owner, repository, canonical_url = parse_github_repository(body.repository_url)
        updated = await _storage.update_source(
            source_id,
            {
                **body.model_dump(),
                "repository_url": canonical_url,
                "repository_owner": owner,
                "repository_name": repository,
            },
        )
    except GitHubImportError as exc:
        raise APIError(
            422, "invalid_field", str(exc), extra={"field": "repository_url"}
        ) from exc
    except ValueError as exc:
        raise APIError(
            409,
            "already_exists",
            "Ya existe una fuente oficial para este repositorio",
            extra={"resource": "official_source"},
        ) from exc
    if not updated:
        raise _not_found()
    return updated


@admin_router.post("/official-sources/{source_id}/sync")
async def admin_sync_official_source(
    source_id: str,
    body: SyncSourceBody | None = None,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    if not await _storage.get_source(source_id):
        raise _not_found()
    try:
        return await _fetch_payload(source_id, body, admin)
    except KeyError as exc:
        raise _not_found() from exc
    except GitHubImportError as exc:
        raise APIError(422, "official_source_sync_failed", str(exc)) from exc
    except ValueError as exc:
        raise APIError(422, "invalid_field", str(exc), extra={"field": "component_ids"}) from exc


@admin_router.delete("/official-sources/{source_id}")
async def admin_delete_official_source(
    source_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    if not await _storage.get_source(source_id):
        raise _not_found()
    removed = await _materializer.remove_all(source_id)
    await _storage.delete_source(source_id)
    return {"ok": True, "removed_resources": removed}


@admin_router.post("/resources/{resource_type}/{resource_id}/official")
async def admin_mark_resource_official(
    resource_type: str,
    resource_id: str,
    body: MarkOfficialBody | None = None,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    """Marca a mano un recurso como oficial, sin repositorio detrás."""
    if resource_type not in OFFICIAL_RESOURCE_TABLES:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de recurso no válido",
            extra={"field": "resource_type"},
        )
    official = body.official if body else True
    source_id = None
    if official:
        source_id = str((await _storage.ensure_internal_source())["id"])
    await _storage.mark_resource(
        resource_type,
        resource_id,
        admin,
        source_id=source_id,
        component_id=resource_id if official else None,
    )
    return {
        "ok": True,
        "official_source_id": source_id,
        "internal_source_id": INTERNAL_SOURCE_ID,
    }
