"""Typed cursor endpoints used by first-party catalog clients."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.routes.agent_imports import load_agent_import_catalog_page
from app.api.routes.auth import (
    GroupContext,
    require_admin,
    require_auth,
    require_group_session,
    require_session,
)
from app.api.routes.explore._shared import _validate_relation
from app.auth.auth import get_user_role
from app.errors import APIError
from app.models.agent_import import AgentImportResourceKind
from app.pagination.api import CursorPageMetadata, CursorPageResponse
from app.pagination.http import cursor_error
from app.pagination.models import CursorPage, CursorParams
from app.pagination.query import scoped_cursor_params
from app.pagination.total import ExactTotalTimeout
from app.services.admin_explore_cursor_listing import list_admin_explore_cursor
from app.services.connection_cursor_listing import list_connections_cursor
from app.services.explore_cursor_listing import (
    build_explore_query,
    list_explore_cursor,
)
from app.services.feed_cursor_listing import list_feed_cursor
from app.services.first_party_catalog import (
    load_agents_catalog_page,
    load_prompts_catalog_page,
    load_skills_catalog_page,
    load_tools_catalog_page,
)
from app.services.knowledge_listing import list_authenticated_knowledge_cursor
from app.services.knowledge_pack_listing import (
    list_authenticated_knowledge_packs_cursor,
)
from app.services.log_listing import build_log_where, list_logs_cursor
from app.services.metadata_cursor_listing import (
    ADMIN_HIDDEN_COLUMNS,
    list_metadata_cursor,
)
from app.services.user_directory_listing import list_user_directory_cursor
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.official_source_storage import OfficialSourceStorage
from app.utils import flog

router = APIRouter(prefix="/api/v2")
_knowledge = KnowledgeStorage()
_knowledge_packs = KnowledgePackStorage()
_official_sources = OfficialSourceStorage()
_groups = GroupStorage()


class ExploreCursorResponse(BaseModel):
    items: list[dict[str, Any]]
    page: CursorPageMetadata
    linked_matches: int = 0


class MetadataCursorResponse(BaseModel):
    columns: list[str]
    items: list[list[Any]]
    page: CursorPageMetadata


class AdminExploreCursorResponse(BaseModel):
    items: list[dict[str, Any]]
    page: CursorPageMetadata
    counts: dict[str, int] | None = None


@router.get("/agents", tags=["agents-v2"])
async def list_agents_v2(
    scope: str = "all",
    label: Optional[str] = None,
    group_id: Optional[str] = None,
    include_inactive: bool = False,
    page: CursorParams = Depends(scoped_cursor_params),
    ctx: GroupContext = Depends(require_group_session),
) -> CursorPageResponse[dict[str, Any]]:
    result = await load_agents_catalog_page(
        scope=scope,
        label=label,
        group_id=group_id,
        include_inactive=include_inactive,
        page=page,
        ctx=ctx,
    )
    return CursorPageResponse.from_result(result, limit=page.limit)


async def _scoped_page(
    loader: Callable[..., Awaitable[CursorPage[dict[str, Any]]]],
    *,
    scope: str,
    group_id: Optional[str],
    page: CursorParams,
    ctx: GroupContext,
) -> CursorPageResponse[dict[str, Any]]:
    result = await loader(
        scope=scope,
        page=page,
        group_id=group_id,
        ctx=ctx,
    )
    return CursorPageResponse.from_result(result, limit=page.limit)


@router.get("/skills", tags=["skills-v2"])
async def list_skills_v2(
    scope: str = "all",
    group_id: Optional[str] = None,
    page: CursorParams = Depends(scoped_cursor_params),
    ctx: GroupContext = Depends(require_group_session),
) -> CursorPageResponse[dict[str, Any]]:
    return await _scoped_page(
        load_skills_catalog_page,
        scope=scope,
        group_id=group_id,
        page=page,
        ctx=ctx,
    )


@router.get("/prompts", tags=["prompts-v2"])
async def list_prompts_v2(
    scope: str = "all",
    group_id: Optional[str] = None,
    page: CursorParams = Depends(scoped_cursor_params),
    ctx: GroupContext = Depends(require_group_session),
) -> CursorPageResponse[dict[str, Any]]:
    return await _scoped_page(
        load_prompts_catalog_page,
        scope=scope,
        group_id=group_id,
        page=page,
        ctx=ctx,
    )


@router.get("/tools", tags=["tools-v2"])
async def list_tools_v2(
    scope: str = "all",
    group_id: Optional[str] = None,
    page: CursorParams = Depends(scoped_cursor_params),
    ctx: GroupContext = Depends(require_group_session),
) -> CursorPageResponse[dict[str, Any]]:
    return await _scoped_page(
        load_tools_catalog_page,
        scope=scope,
        group_id=group_id,
        page=page,
        ctx=ctx,
    )


@router.get("/knowledge", tags=["knowledge-v2"])
async def list_knowledge_v2(
    type: Optional[str] = None,
    owner_scope: str = "group",
    group_id: Optional[str] = None,
    page: CursorParams = Depends(scoped_cursor_params),
    ctx: GroupContext = Depends(require_group_session),
) -> CursorPageResponse[dict[str, Any]]:
    result = await list_authenticated_knowledge_cursor(
        _knowledge,
        ctx=ctx,
        owner_scope=owner_scope,
        type=type,
        page=page,
        requested_group_id=group_id,
    )
    return CursorPageResponse.from_result(result, limit=page.limit)


@router.get("/knowledge-packs", tags=["knowledge-v2"])
async def list_knowledge_packs_v2(
    group_id: Optional[str] = None,
    page: CursorParams = Depends(scoped_cursor_params),
    ctx: GroupContext = Depends(require_group_session),
) -> CursorPageResponse[dict[str, Any]]:
    result = await list_authenticated_knowledge_packs_cursor(
        _knowledge_packs,
        ctx=ctx,
        page=page,
        requested_group_id=group_id,
    )
    return CursorPageResponse.from_result(result, limit=page.limit)


@router.get("/agents/import/catalog/{kind}", tags=["agents-v2"])
async def search_agent_import_catalog_v2(
    kind: AgentImportResourceKind,
    q: str = Query(default="", max_length=200),
    page_params: CursorParams = Depends(scoped_cursor_params),
    ctx: GroupContext = Depends(require_group_session),
) -> CursorPageResponse:
    page = await load_agent_import_catalog_page(kind, q, page_params, ctx)
    return CursorPageResponse.from_result(page, limit=page_params.limit)


@router.get("/explore", tags=["explore-v2"])
async def list_explore_v2(
    type: Optional[str] = Query(None, max_length=50),
    category: Optional[str] = Query(None, max_length=100),
    q: Optional[str] = Query(None, max_length=200),
    tag: Optional[str] = Query(None, max_length=100),
    label: Optional[list[str]] = Query(None),
    language: Optional[list[str]] = Query(None),
    include_official: bool = True,
    pack_mode: Optional[bool] = None,
    relation: Optional[str] = None,
    page: CursorParams = Depends(scoped_cursor_params),
    username: str = Depends(require_session),
) -> ExploreCursorResponse:
    relation_mode = _validate_relation(relation)
    query = build_explore_query(
        username=username,
        type=type,
        category=category,
        q=q,
        tag=tag,
        labels=label,
        languages=language,
        include_official=include_official,
        pack_mode=pack_mode,
        relation=relation_mode,
    )
    try:
        result, linked_matches = await list_explore_cursor(
            query=query,
            username=username,
            page=page,
            knowledge=_knowledge,
        )
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource="explore") from exc
    response = CursorPageResponse.from_result(result, limit=page.limit)
    return ExploreCursorResponse(
        items=response.items,
        page=response.page,
        linked_matches=linked_matches,
    )


@router.get("/users", tags=["users-v2"])
async def search_users_v2(
    q: str | None = Query(None, max_length=200),
    page: CursorParams = Depends(scoped_cursor_params),
    username: str = Depends(require_auth),
) -> CursorPageResponse[dict[str, Any]]:
    try:
        result = await list_user_directory_cursor(
            requester=username,
            query=q,
            page=page,
        )
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource="user") from exc
    return CursorPageResponse.from_result(result, limit=page.limit)


@router.get("/feed", tags=["explore-v2"])
async def list_feed_v2(
    type: Optional[str] = Query(None, max_length=50),
    page: CursorParams = Depends(scoped_cursor_params),
    username: str = Depends(require_auth),
) -> CursorPageResponse[dict[str, Any]]:
    try:
        result = await list_feed_cursor(
            username=username, resource_type=type, page=page
        )
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource="feed") from exc
    return CursorPageResponse.from_result(result, limit=page.limit)


@router.get("/connections", tags=["connections-v2"])
async def list_connections_v2(
    group_id: str | None = Query(None, max_length=200),
    include_inactive: bool = False,
    include_models: bool = False,
    page: CursorParams = Depends(scoped_cursor_params),
    ctx: GroupContext = Depends(require_group_session),
) -> CursorPageResponse[dict[str, Any]]:
    if group_id is not None:
        role = await get_user_role(ctx.user)
        if role != "admin" and not await _groups.can_access(group_id, ctx.user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
    try:
        result = await list_connections_cursor(
            user=ctx.user,
            group_id=ctx.group_id,
            requested_group_id=group_id,
            include_inactive=include_inactive,
            include_models=include_models,
            page=page,
        )
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource="connection") from exc
    return CursorPageResponse.from_result(result, limit=page.limit)


@router.get(
    "/admin/metadata/tables/{table_name}/data", tags=["admin-metadata-v2"]
)
async def list_admin_metadata_table_v2(
    table_name: str,
    q: str | None = Query(None, max_length=500),
    page: CursorParams = Depends(scoped_cursor_params),
    admin: str = Depends(require_admin),
) -> MetadataCursorResponse:
    try:
        result = await list_metadata_cursor(
            admin=admin,
            table_name=table_name,
            query=q,
            hidden_columns=ADMIN_HIDDEN_COLUMNS,
            page=page,
        )
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource="admin_metadata") from exc
    response = CursorPageResponse.from_result(result.page, limit=page.limit)
    return MetadataCursorResponse(
        columns=result.columns, items=response.items, page=response.page
    )


@router.get("/admin/explore", tags=["admin-explore-v2"])
async def list_admin_explore_v2(
    type: list[str] | None = Query(None),
    q: str | None = Query(None, max_length=500),
    owner: str | None = Query(None, max_length=200),
    role: str | None = Query(None, max_length=30),
    active: bool | None = Query(None),
    verified: bool | None = Query(None),
    knowledge_type: str | None = Query(None, max_length=50),
    include_counts: bool = False,
    page: CursorParams = Depends(scoped_cursor_params),
    admin: str = Depends(require_admin),
) -> AdminExploreCursorResponse:
    try:
        result = await list_admin_explore_cursor(
            admin=admin,
            resource_types=type or (),
            query=q,
            owner=owner,
            role=role,
            active=active,
            verified=verified,
            knowledge_type=knowledge_type,
            include_counts=include_counts,
            page=page,
        )
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource="admin_explore") from exc
    response = CursorPageResponse.from_result(result.page, limit=page.limit)
    return AdminExploreCursorResponse(
        items=response.items, page=response.page, counts=result.counts
    )


@router.get("/admin/logs", tags=["admin-logs-v2"])
async def list_logs_v2(
    date_from: Optional[str] = Query(None, max_length=10),
    date_to: Optional[str] = Query(None, max_length=10),
    ip: Optional[str] = Query(None, max_length=100),
    username: Optional[str] = Query(None, max_length=100),
    level: Optional[str] = Query(None, max_length=20),
    source: Optional[str] = Query(None, max_length=20),
    category: Optional[str] = Query(None, max_length=30),
    action: Optional[str] = Query(None, max_length=200),
    resource_type: Optional[str] = Query(None, max_length=100),
    resource_id: Optional[str] = Query(None, max_length=300),
    outcome: Optional[str] = Query(None, max_length=30),
    q: Optional[str] = Query(None, max_length=500),
    page: CursorParams = Depends(scoped_cursor_params),
    admin: str = Depends(require_admin),
) -> CursorPageResponse[dict[str, Any]]:
    await asyncio.to_thread(flog.flush)
    where, params = build_log_where(
        date_from,
        date_to,
        ip,
        username,
        level,
        source,
        category,
        action,
        resource_type,
        resource_id,
        outcome,
        q,
    )
    try:
        result = await list_logs_cursor(
            admin=admin,
            where=where,
            params=params,
            page=page,
        )
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource="log") from exc
    return CursorPageResponse.from_result(result, limit=page.limit)


@router.get(
    "/admin/official-source-drafts/{draft_id}/components",
    tags=["admin-official-sources-v2"],
)
async def list_official_source_draft_components_v2(
    draft_id: str,
    component_type: Optional[str] = Query(None, max_length=100),
    state: Optional[str] = Query(None, max_length=50),
    q: str = Query("", max_length=500),
    page: CursorParams = Depends(scoped_cursor_params),
    admin: str = Depends(require_admin),
) -> CursorPageResponse[dict[str, Any]]:
    draft = await _official_sources.get_draft(draft_id)
    if not draft:
        raise APIError(404, "not_found", "Borrador de importación no encontrado")
    if draft["owner_id"] != admin:
        raise APIError(403, "forbidden", "El borrador pertenece a otro administrador")
    try:
        result = await _official_sources.list_draft_components_cursor(
            draft_id,
            page=page,
            component_type=component_type,
            state=state,
            query=q,
        )
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource="official_import_component") from exc
    return CursorPageResponse.from_result(result, limit=page.limit)
