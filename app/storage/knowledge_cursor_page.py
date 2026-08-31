"""Keyset pagination for the visible Knowledge catalog."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.services.resource_visibility import VisibilityFilter
from app.storage.cursor_page_query import fetch_cursor_page
from app.storage.db import open_db
from app.storage.knowledge_page_spec import knowledge_page_query

AnnotatePage = Callable[..., Awaitable[None]]
DecodeRow = Callable[[Any], dict[str, Any]]


async def fetch_visible_knowledge_cursor_page(
    *,
    user: str,
    owner_id: str,
    type: str | None,
    page: CursorParams,
    permission_filter: VisibilityFilter | None,
    requested_group_id: str | None,
    catalog_filter: VisibilityFilter | None,
    decode: DecodeRow,
    annotate: AnnotatePage,
) -> CursorPage[dict[str, Any]]:
    """Return one signed keyset page and annotate its shared resources."""

    query = knowledge_page_query(
        user=user,
        owner_id=owner_id,
        type=type,
        permission_filter=permission_filter,
        requested_group_id=requested_group_id,
        catalog_filter=catalog_filter,
    )
    context = cursor_context_signature(
        {
            "resource": "knowledge",
            "user": user,
            "owner_id": owner_id,
            "type": type,
            "requested_group_id": requested_group_id,
            "where": query.where,
            "params": query.params,
            "consistent": page.consistent,
        }
    )
    async with open_db() as conn:
        result = await fetch_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM knowledge_items k WHERE {query.where}",
            select_sql=(
                f"SELECT {query.columns} FROM knowledge_items k WHERE {query.where}"
            ),
            params=query.params,
            position_column="k.created_at",
            id_column="k.id",
            context=context,
            resource="knowledge",
            page=page,
            decode=decode,
        )
        await annotate(
            conn,
            result.items,
            user=user,
            owner_id=owner_id,
            requested_group_id=requested_group_id,
        )
    return result
