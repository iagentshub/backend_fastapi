"""Consulta paginada común para agents/skills/prompts/tools con scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.pagination.models import OffsetPage, OffsetParams
from app.services.resource_visibility import (
    VisibilityFilter,
    annotate_shared_items,
    build_visibility_filter,
)
from app.storage.db import open_db
from app.storage.page_query import fetch_offset_page


@dataclass(frozen=True, slots=True)
class ScopedResourcePageSpec:
    table: str
    columns: str
    resource_type: str
    decode: Callable[[Any], dict[str, Any]]


async def list_scoped_resource_page(
    spec: ScopedResourcePageSpec,
    *,
    user: str,
    active_group_id: str,
    scope: str,
    include_inactive: bool,
    page: OffsetParams,
    requested_group_id: str | None = None,
    extra_filters: tuple[VisibilityFilter, ...] = (),
    include_public: bool = True,
) -> OffsetPage[dict[str, Any]]:
    alias = "resource_row"
    visibility = build_visibility_filter(
        alias=alias,
        user=user,
        active_group_id=active_group_id,
        resource_type=spec.resource_type,
        requested_group_id=requested_group_id,
        include_public=include_public,
    )
    clauses = [visibility.sql]
    params = list(visibility.params)
    if scope in ("public", "private"):
        clauses.append(f"{alias}.scope = ?")
        params.append(scope)
    if not include_inactive:
        clauses.append(f"{alias}.is_active = 1")
    for extra_filter in extra_filters:
        clauses.append(extra_filter.sql)
        params.extend(extra_filter.params)
    where = " AND ".join(f"({clause})" for clause in clauses)
    async with open_db() as conn:
        result = await fetch_offset_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM {spec.table} {alias} WHERE {where}",
            select_sql=(
                f"SELECT {spec.columns} FROM {spec.table} {alias} WHERE {where} "
                f"ORDER BY {alias}.updated_at DESC, {alias}.id DESC"
            ),
            params=tuple(params),
            page=page,
            decode=spec.decode,
        )
        await annotate_shared_items(
            conn,
            result.items,
            user=user,
            active_group_id=active_group_id,
            resource_type=spec.resource_type,
            requested_group_id=requested_group_id,
        )
    return result
