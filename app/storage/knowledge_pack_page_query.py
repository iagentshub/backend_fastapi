"""Consultas paginadas compartidas del catálogo de Knowledge Packs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.services.resource_visibility import VisibilityFilter
from app.storage.cursor_page_query import fetch_cursor_page
from app.storage.db import open_db


@dataclass(frozen=True, slots=True)
class PackPageQuery:
    visible_join: str
    where: str
    params: tuple[Any, ...]
    columns: str


def pack_page_query(
    owner_id: str,
    username: str,
    catalog_filter: VisibilityFilter | None,
    requested_group_id: str | None = None,
) -> PackPageQuery:
    if requested_group_id is not None:
        visible_join = (
            "LEFT JOIN (SELECT s.resource_id,MIN(s.group_id) AS shared_group_id "
            "FROM resource_group_shares s JOIN groups g ON g.id=s.group_id "
            "WHERE s.resource_type='knowledge_pack' AND s.group_id=? "
            "AND g.is_active=1 GROUP BY s.resource_id) visible "
            "ON visible.resource_id=p.id"
        )
        clauses = [
            "visible.resource_id IS NOT NULL",
            "COALESCE(p.upload_status, 'ready')='ready'",
        ]
        params: list[Any] = [requested_group_id]
    else:
        visible_join = (
            "LEFT JOIN (SELECT s.resource_id,MIN(s.group_id) AS shared_group_id "
            "FROM resource_group_shares s "
            "JOIN group_members m ON m.group_id=s.group_id "
            "JOIN groups g ON g.id=s.group_id "
            "WHERE s.resource_type='knowledge_pack' "
            "AND m.username=? AND g.is_active=1 GROUP BY s.resource_id) visible "
            "ON visible.resource_id=p.id"
        )
        clauses = [
            "(p.owner_id=? OR visible.resource_id IS NOT NULL)",
            "COALESCE(p.upload_status, 'ready')='ready'",
        ]
        params = [username, owner_id]
    if catalog_filter is not None:
        clauses.append(catalog_filter.sql)
        params.extend(catalog_filter.params)
    return PackPageQuery(
        visible_join=visible_join,
        where=" AND ".join(f"({clause})" for clause in clauses),
        params=tuple(params),
        columns=(
            "p.*, "
            "(SELECT COUNT(*) FROM knowledge_items k WHERE k.pack_id=p.id) "
            "AS file_count, "
            "(SELECT COALESCE(SUM(k.size_bytes),0) FROM knowledge_items k "
            "WHERE k.pack_id=p.id) AS size_bytes, "
            "visible.shared_group_id"
        ),
    )


async def fetch_pack_cursor_page(
    owner_id: str,
    username: str,
    *,
    page: CursorParams,
    catalog_filter: VisibilityFilter | None,
    decode: Callable[[Any], dict[str, Any]],
    requested_group_id: str | None = None,
) -> CursorPage[dict[str, Any]]:
    query = pack_page_query(
        owner_id, username, catalog_filter, requested_group_id=requested_group_id
    )
    context = cursor_context_signature(
        {
            "resource": "knowledge_pack",
            "owner_id": owner_id,
            "username": username,
            "requested_group_id": requested_group_id,
            "where": query.where,
            "params": query.params,
            "consistent": page.consistent,
        }
    )
    async with open_db() as conn:
        result = await fetch_cursor_page(
            conn,
            count_sql=(
                f"SELECT COUNT(*) FROM knowledge_packs p {query.visible_join} "
                f"WHERE {query.where}"
            ),
            select_sql=(
                f"SELECT {query.columns} FROM knowledge_packs p "
                f"{query.visible_join} WHERE {query.where}"
            ),
            params=query.params,
            position_column="p.created_at",
            id_column="p.id",
            context=context,
            resource="knowledge_pack",
            page=page,
            decode=decode,
        )
    for pack in result.items:
        shared_group_id = pack.pop("shared_group_id", None)
        if str(pack.get("owner_id") or "") != owner_id and shared_group_id:
            pack["_shared"] = True
            pack["_group_id"] = str(shared_group_id)
    return result
