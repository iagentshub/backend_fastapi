"""Authenticated, non-persistent preview of local agent files."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import ValidationError

from app.api.routes.auth import GroupContext, require_group_session
from app.errors import APIError
from app.models.agent_import import (
    AgentDirectoryApplyOptions,
    AgentDirectoryImportPlan,
    AgentDirectoryImportResult,
    AgentImportCatalogPage,
    AgentImportCatalogResolveRequest,
    AgentImportPreview,
    AgentImportResourceKind,
)
from app.pagination.models import OffsetParams
from app.services.agent_directory_import import (
    AgentDirectoryImportService,
    uploads_from_parts,
)
from app.services.agent_import import parse_agent_import
from app.services.agent_import_catalog import AgentImportCatalog
from app.services.agent_import_sessions import (
    claim_import_session,
    create_import_session,
)

router = APIRouter(prefix="/api/agents/import", tags=["agents"])


@router.get("/catalog/{kind}", response_model=AgentImportCatalogPage)
async def search_agent_import_catalog(
    kind: AgentImportResourceKind,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: GroupContext = Depends(require_group_session),
) -> AgentImportCatalogPage:
    """Search one resource type; catalogs remain independently pageable."""

    page = await AgentImportCatalog.search_page(
        ctx, kind, query=q, page=OffsetParams(limit=limit, offset=offset)
    )
    return AgentImportCatalogPage(
        items=list(page.items),
        total=page.total,
        limit=limit,
        offset=offset,
        has_more=page.has_more,
    )


@router.post("/catalog/resolve")
async def resolve_agent_import_catalog(
    payload: AgentImportCatalogResolveRequest,
    ctx: GroupContext = Depends(require_group_session),
) -> dict[str, list[dict]]:
    """Resolve linked IDs in batched, visibility-filtered queries per type."""

    return await AgentImportCatalog.resolve_rows(ctx, payload.resources)


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
    queries = {}
    for reference in preview.references:
        queries.setdefault(reference.kind, []).append(reference.source)
    catalog = await AgentImportCatalog.load_for_queries(ctx, queries)
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
    plan, detected = await AgentDirectoryImportService().plan(uploads, ctx)
    session_id = await create_import_session(plan, detected, ctx)
    return plan.model_copy(update={"session_id": session_id})


@router.post("/directory/apply", response_model=AgentDirectoryImportResult)
async def apply_agent_directory(
    files: list[UploadFile] | None = File(default=None),
    paths: str = Form(default="[]"),
    options: str = Form(...),
    session_id: str | None = Form(default=None),
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
    service = AgentDirectoryImportService()
    if session_id:
        async with claim_import_session(session_id, ctx) as prepared:
            return await service.apply(
                None, parsed_options, ctx, prepared=prepared
            )
    uploads = await _directory_uploads(files or [], paths)
    return await service.apply(uploads, parsed_options, ctx)
