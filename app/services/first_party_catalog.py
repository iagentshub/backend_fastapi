"""Adaptadores tipados del catálogo cursor para clientes propios."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.api.routes.auth import GroupContext
from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.middleware.locale import get_locale
from app.pagination.models import CursorPage, CursorParams
from app.services.agent_listing import load_authenticated_agents_page
from app.services.agent_presentation import apply_agent_locale, validate_agent_scope
from app.services.scoped_resource_listing import (
    VisiblePageStorage,
    load_authenticated_scoped_resource_page,
)
from app.storage.agent_storage import AgentStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.utils.origin import compute_origin_type

_agents = AgentStorage(AGENTS_DIR)
_skills = SkillStorage(SKILLS_DIR)
_prompts = PromptStorage()
_tools = ToolStorage()


def _mark_origin(item: dict[str, Any], user: str, group_id: str) -> None:
    if item.get("_shared") or item.get("owner_id") in (user, group_id):
        item["origin_type"] = compute_origin_type(item)


async def load_agents_catalog_page(
    *,
    scope: str,
    label: str | None,
    group_id: str | None,
    include_inactive: bool,
    page: CursorParams,
    ctx: GroupContext,
) -> CursorPage[dict[str, Any]]:
    validate_agent_scope(scope)
    locale = get_locale()
    return await load_authenticated_agents_page(
        _agents,
        ctx=ctx,
        scope=scope,
        label=label,
        include_inactive=include_inactive,
        page=page,
        requested_group_id=group_id,
        present=lambda item: apply_agent_locale(item, locale, AGENTS_DIR),
    )


async def _load_scoped_catalog_page(
    storage: VisiblePageStorage,
    *,
    scope: str,
    group_id: str | None,
    page: CursorParams,
    ctx: GroupContext,
) -> CursorPage[dict[str, Any]]:
    validate_agent_scope(scope)
    return await load_authenticated_scoped_resource_page(
        storage,
        ctx=ctx,
        scope=scope,
        page=page,
        requested_group_id=group_id,
        mark_origin=_mark_origin,
    )


ScopedCatalogLoader = Callable[..., Awaitable[CursorPage[dict[str, Any]]]]


def _scoped_loader(storage: VisiblePageStorage) -> ScopedCatalogLoader:
    async def load(
        *,
        scope: str,
        group_id: str | None,
        page: CursorParams,
        ctx: GroupContext,
    ) -> CursorPage[dict[str, Any]]:
        return await _load_scoped_catalog_page(
            storage,
            scope=scope,
            group_id=group_id,
            page=page,
            ctx=ctx,
        )

    return load


load_skills_catalog_page = _scoped_loader(_skills)
load_prompts_catalog_page = _scoped_loader(_prompts)
load_tools_catalog_page = _scoped_loader(_tools)
