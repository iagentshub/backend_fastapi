"""Authenticated, non-persistent preview of local agent files."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import ValidationError

from app.api.routes.auth import GroupContext, require_group_session
from app.errors import APIError
from app.models.agent_import import (
    AgentDirectoryApplyOptions,
    AgentDirectoryImportPlan,
    AgentDirectoryImportResult,
    AgentImportPreview,
)
from app.services.agent_directory_import import (
    AgentDirectoryImportService,
    uploads_from_parts,
)
from app.services.agent_import import parse_agent_import
from app.services.agent_import_catalog import AgentImportCatalog

router = APIRouter(prefix="/api/agents/import", tags=["agents"])


@router.post("/preview", response_model=AgentImportPreview)
async def preview_agent_import(
    file: UploadFile = File(...),
    ctx: GroupContext = Depends(require_group_session),
) -> AgentImportPreview:
    """Inspect one file and return an editable draft without saving it."""

    # El único techo de subida es `max_request_bytes`, aplicado antes de esta
    # ruta por BodySizeLimitMiddleware. Su valor por defecto es 0 (sin límite)
    # y el administrador puede cambiarlo en caliente desde el panel.
    content = await file.read()
    preview = parse_agent_import(file.filename or "agent.md", content)
    catalog = await AgentImportCatalog.load(ctx)
    return catalog.resolve_preview(preview)


async def _directory_uploads(
    files: list[UploadFile], paths_json: str
) -> list[tuple[str, bytes]]:
    try:
        paths = json.loads(paths_json)
    except json.JSONDecodeError:
        paths = None
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise APIError(
            422,
            "invalid_field",
            "La lista de rutas de la carpeta no es válida",
            extra={"field": "paths", "reason": "invalid_paths"},
        )
    contents = [await upload.read() for upload in files]
    return uploads_from_parts(paths, contents)


@router.post("/directory/preview", response_model=AgentDirectoryImportPlan)
async def preview_agent_directory(
    files: list[UploadFile] = File(...),
    paths: str = Form(...),
    ctx: GroupContext = Depends(require_group_session),
) -> AgentDirectoryImportPlan:
    """Discover all agents and their shared local/existing dependencies."""

    uploads = await _directory_uploads(files, paths)
    plan, _ = await AgentDirectoryImportService().plan(uploads, ctx)
    return plan


@router.post("/directory/apply", response_model=AgentDirectoryImportResult)
async def apply_agent_directory(
    files: list[UploadFile] = File(...),
    paths: str = Form(...),
    options: str = Form(...),
    ctx: GroupContext = Depends(require_group_session),
) -> AgentDirectoryImportResult:
    """Apply the reviewed graph in a single database transaction."""

    try:
        parsed_options = AgentDirectoryApplyOptions.model_validate_json(options)
    except ValidationError:
        raise APIError(
            422,
            "invalid_field",
            "Las opciones de importación no son válidas",
            extra={"field": "options", "reason": "invalid_options"},
        ) from None
    uploads = await _directory_uploads(files, paths)
    return await AgentDirectoryImportService().apply(uploads, parsed_options, ctx)
