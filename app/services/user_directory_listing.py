"""Listado cursor del directorio público de usuarios."""

from __future__ import annotations

from typing import Any

from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.storage.composite_cursor_page import KeysetColumn, fetch_composite_cursor_page
from app.storage.db import open_db


async def list_user_directory_cursor(
    *, requester: str, query: str | None, page: CursorParams
) -> CursorPage[dict[str, Any]]:
    pattern = f"%{query}%" if query else None
    where = "u.id != ? AND u.role <> 'guest'"
    params: tuple[Any, ...] = (requester,)
    if pattern is not None:
        where += " AND LOWER(u.username) LIKE LOWER(?)"
        params = (*params, pattern)
    context = cursor_context_signature(
        {
            "resource": "users",
            "requester": requester,
            "query": query or "",
        }
    )
    async with open_db() as conn:
        return await fetch_composite_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM users u WHERE {where}",
            select_sql=(
                "SELECT u.id,u.username,(SELECT COUNT(*) FROM user_avatars "
                "WHERE owner_id=u.id) AS has_avatar,(SELECT COUNT(*) "
                "FROM user_follows WHERE following=u.id) AS followers_count,"
                "(SELECT COUNT(*) FROM resource_social "
                "WHERE owner IN (u.id,u.username) AND is_public=1) "
                f"AS public_resources_count FROM users u WHERE {where}"
            ),
            params=params,
            columns=(
                KeysetColumn("u.username", "username", descending=False),
                KeysetColumn("u.id", "id", descending=False),
            ),
            context=context,
            resource="user",
            page=page,
            decode=lambda row: {
                "username": row["username"],
                "avatar_url": (
                    f"/api/users/{row['username']}/avatar"
                    if row["has_avatar"]
                    else None
                ),
                "followers_count": row["followers_count"],
                "public_resources_count": row["public_resources_count"],
            },
        )
