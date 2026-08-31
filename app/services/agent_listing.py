"""Listado paginado de agentes con filtros y permisos resueltos antes del LIMIT."""

from __future__ import annotations

from typing import Any, Callable

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.pagination.metrics import increment
from app.pagination.models import CursorPage, CursorParams
from app.pagination.total import ExactTotalTimeout
from app.services.resource_visibility import (
    VisibilityFilter,
    build_permission_filter,
)
from app.storage.agent_storage import AgentStorage
from app.storage.groups import GroupStorage
from app.utils.origin import compute_origin_type


async def load_authenticated_agents_page(
    storage: AgentStorage,
    *,
    ctx: GroupContext,
    scope: str,
    label: str | None,
    include_inactive: bool,
    page: CursorParams,
    requested_group_id: str | None,
    present: Callable[[dict[str, Any]], dict[str, Any]],
) -> CursorPage[dict[str, Any]]:
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

    try:
        result = await storage.list_visible_page(
            user=ctx.user,
            active_group_id=ctx.group_id,
            scope=scope,
            include_inactive=include_inactive,
            page=page,
            requested_group_id=requested_group_id,
            extra_filters=tuple(filters),
        )
    except ExactTotalTimeout as exc:
        raise APIError(
            503,
            "pagination_total_timeout",
            "El total exacto no estuvo disponible dentro del tiempo permitido",
            extra={"resource": "agent"},
        ) from exc
    except ValueError as exc:
        increment("agent", "invalid_cursors")
        raise APIError(422, "invalid_cursor", "Cursor no válido") from exc
    enriched: list[dict[str, Any]] = []
    for item in result.items:
        visible = present(item)
        visible["origin_type"] = compute_origin_type(visible)
        enriched.append(visible)
    return CursorPage(
        items=enriched,
        next_cursor=result.next_cursor,
        has_more=result.has_more,
        total=result.total,
        snapshot_at=result.snapshot_at,
    )
