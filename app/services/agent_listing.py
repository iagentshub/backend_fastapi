"""Listado paginado de agentes con filtros y permisos resueltos antes del LIMIT."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Response

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.pagination.http import publish_offset_page
from app.pagination.models import OffsetParams
from app.services.resource_visibility import (
    VisibilityFilter,
    build_permission_filter,
)
from app.storage.agent_storage import AgentStorage
from app.storage.groups import GroupStorage
from app.utils.origin import compute_origin_type


async def list_authenticated_agents(
    storage: AgentStorage,
    *,
    ctx: GroupContext,
    scope: str,
    label: str | None,
    include_inactive: bool,
    page: OffsetParams,
    response: Response | None,
    requested_group_id: str | None,
    present: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    groups = GroupStorage()
    role = await get_user_role(ctx.user)
    if requested_group_id is not None and role != "admin":
        if not await groups.can_access(requested_group_id, ctx.user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")

    filters: list[VisibilityFilter] = []
    if label:
        filters.append(VisibilityFilter("resource_row.data LIKE ?", (f'%"{label}"%',)))
    if ctx.group_id != ctx.user and role != "admin":
        member = await groups.get_member(ctx.group_id, ctx.user)
        filters.append(
            build_permission_filter(
                member,
                alias="resource_row",
                section="agents",
                action="use",
            )
        )

    result = await storage.list_visible_page(
        user=ctx.user,
        active_group_id=ctx.group_id,
        scope=scope,
        include_inactive=include_inactive,
        page=page,
        requested_group_id=requested_group_id,
        extra_filters=tuple(filters),
    )
    publish_offset_page(response, result)
    enriched: list[dict[str, Any]] = []
    for item in result.items:
        visible = present(item)
        visible["origin_type"] = compute_origin_type(visible)
        enriched.append(visible)
    return enriched
