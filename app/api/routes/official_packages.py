"""Catálogo oficial, copias privadas y exportaciones por IDE."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.routes.auth import require_auth
from app.errors import APIError
from app.services.official_package_exporter import (
    OfficialPackageCopier,
    build_export_plan,
    export_plan_zip,
)
from app.storage.official_package_storage import OfficialPackageStorage

router = APIRouter(prefix="/api/official-packages", tags=["official-packages"])
_storage = OfficialPackageStorage()


class ComponentSelectionBody(BaseModel):
    component_ids: List[str] = Field(default_factory=list, max_length=500)


class ExportPreviewBody(ComponentSelectionBody):
    target: Literal["codex", "claude", "cursor"]


async def _published(package_id: str, *, content: bool = False) -> Dict[str, Any]:
    package = await _storage.get_published(package_id, include_content=content)
    if not package:
        raise APIError(
            404,
            "not_found",
            "Paquete oficial no encontrado",
            extra={"resource": "official_package"},
        )
    return package


@router.get("")
async def list_official_packages(
    _: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    return await _storage.list_packages(published_only=True)


@router.get("/copies")
async def list_official_package_copies(
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    return await OfficialPackageCopier(_storage).list_for_user(username)


@router.get("/components")
async def list_official_components(
    _: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    return await _storage.list_published_components()


@router.get("/{package_id}")
async def get_official_package(
    package_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    return await _published(package_id)


@router.post("/{package_id}/copy")
async def copy_official_package(
    package_id: str,
    body: ComponentSelectionBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    try:
        return await OfficialPackageCopier(_storage).copy(
            package_id, username, body.component_ids
        )
    except KeyError as exc:
        raise APIError(404, "not_found", "Paquete oficial no encontrado") from exc


@router.post("/{package_id}/link")
async def link_official_package(
    package_id: str,
    body: ComponentSelectionBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    try:
        return await OfficialPackageCopier(_storage).link(
            package_id, username, body.component_ids
        )
    except KeyError as exc:
        raise APIError(
            404,
            "not_found",
            "Paquete oficial no encontrado",
            extra={"resource": "official_package"},
        ) from exc


@router.post("/{package_id}/export-preview")
async def preview_official_package_export(
    package_id: str,
    body: ExportPreviewBody,
    _: str = Depends(require_auth),
) -> Dict[str, Any]:
    package = await _published(package_id, content=True)
    return build_export_plan(package, body.target, body.component_ids)


@router.post("/{package_id}/export/{target}")
async def export_official_package(
    package_id: str,
    target: Literal["codex", "claude", "cursor"],
    body: ComponentSelectionBody,
    _: str = Depends(require_auth),
) -> Response:
    package = await _published(package_id, content=True)
    plan = build_export_plan(package, target, body.component_ids)
    filename = f"{package_id}-{plan['version']}-{target}.zip"
    return Response(
        export_plan_zip(plan),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
