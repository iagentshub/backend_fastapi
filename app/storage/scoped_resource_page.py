"""Consulta paginada común para agents/skills/prompts/tools con scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.services.resource_visibility import (
    VisibilityFilter,
    annotate_shared_items,
    build_visibility_filter,
)
from app.storage.cursor_page_query import fetch_cursor_page
from app.storage.db import open_db


@dataclass(frozen=True, slots=True)
class ScopedResourcePageSpec:
    table: str
    columns: str
    resource_type: str
    decode: Callable[[Any], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _ScopedResourceQuery:
    where: str
    params: tuple[Any, ...]


def _build_scoped_query(
    spec: ScopedResourcePageSpec,
    *,
    user: str,
    active_group_id: str,
    scope: str,
    include_inactive: bool | None,
    requested_group_id: str | None,
    extra_filters: tuple[VisibilityFilter, ...],
    include_public: bool,
) -> _ScopedResourceQuery:
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
    if include_inactive is False:
        clauses.append(f"{alias}.is_active = 1")
    for extra_filter in extra_filters:
        clauses.append(extra_filter.sql)
        params.extend(extra_filter.params)
    return _ScopedResourceQuery(
        where=" AND ".join(f"({clause})" for clause in clauses),
        params=tuple(params),
    )


async def list_scoped_resource_page(
    spec: ScopedResourcePageSpec,
    *,
    user: str,
    active_group_id: str,
    scope: str,
    include_inactive: bool | None,
    page: CursorParams,
    requested_group_id: str | None = None,
    extra_filters: tuple[VisibilityFilter, ...] = (),
    include_public: bool = True,
) -> CursorPage[dict[str, Any]]:
    alias = "resource_row"
    query = _build_scoped_query(
        spec,
        user=user,
        active_group_id=active_group_id,
        scope=scope,
        include_inactive=include_inactive,
        requested_group_id=requested_group_id,
        extra_filters=extra_filters,
        include_public=include_public,
    )
    where, params = query.where, query.params
    async with open_db() as conn:
        context = cursor_context_signature(
            {
                "table": spec.table,
                "user": user,
                "active_group_id": active_group_id,
                "scope": scope,
                "include_inactive": include_inactive,
                "requested_group_id": requested_group_id,
                "include_public": include_public,
                "where": where,
                "params": params,
                "consistent": page.consistent,
            }
        )
        result = await fetch_cursor_page(
            conn,
            count_sql=(
                f"SELECT COUNT(*) FROM {spec.table} {alias} WHERE {where}"
            ),
            select_sql=(
                f"SELECT {spec.columns} FROM {spec.table} {alias} WHERE {where}"
            ),
            params=params,
            position_column=f"{alias}.updated_at",
            id_column=f"{alias}.id",
            context=context,
            resource=spec.resource_type,
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
