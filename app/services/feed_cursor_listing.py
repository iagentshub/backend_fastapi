"""Listado keyset del feed de recursos publicados por usuarios seguidos."""

from __future__ import annotations

import json
from typing import Any

from app.api.routes.explore._shared import STARRED_BY_REQUESTER, _add_owner_usernames
from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.services.social_catalog import _PUBLIC_VAL, PUBLICLY_AVAILABLE_SQL
from app.storage.composite_cursor_page import (
    KeysetColumn,
    SnapshotColumn,
    fetch_composite_cursor_page,
)
from app.storage.db import open_db
from app.utils import now_iso


def _decode_feed_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    for field, fallback in (("tags", []), ("labels", ["private"])):
        try:
            item[field] = json.loads(item.get(field) or json.dumps(fallback))
        except (TypeError, ValueError):
            item[field] = fallback
    item["starred"] = bool(item.get("starred"))
    return item


async def list_feed_cursor(
    *, username: str, resource_type: str | None, page: CursorParams
) -> CursorPage[dict[str, Any]]:
    conditions = [
        "owner IN (SELECT following FROM user_follows WHERE follower = ?)",
        "is_public = ?",
        PUBLICLY_AVAILABLE_SQL,
    ]
    params: list[Any] = [username, _PUBLIC_VAL]
    if resource_type and resource_type != "all":
        conditions.append("resource_type = ?")
        params.append(resource_type)
    where = " AND ".join(conditions)
    context = cursor_context_signature(
        {
            "resource": "feed",
            "username": username,
            "type": resource_type or "",
            "consistent": page.consistent,
        }
    )
    async with open_db() as conn:
        result = await fetch_composite_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM resource_social WHERE {where}",
            select_sql=(
                "SELECT resource_type,resource_id,owner,name,description,category,"
                "stars_count,tags,labels,updated_at,"
                f"{STARRED_BY_REQUESTER} AS starred "
                f"FROM resource_social WHERE {where}"
            ),
            select_params_prefix=(username,),
            params=tuple(params),
            columns=(
                KeysetColumn("updated_at", "updated_at"),
                KeysetColumn("resource_type", "resource_type", descending=False),
                KeysetColumn("resource_id", "resource_id", descending=False),
                KeysetColumn("owner", "owner", descending=False),
            ),
            context=context,
            resource="feed",
            page=page,
            decode=_decode_feed_row,
            snapshot=SnapshotColumn("updated_at", now_iso()),
        )
    items = list(result.items)
    await _add_owner_usernames(items)
    return CursorPage(
        items=items,
        next_cursor=result.next_cursor,
        has_more=result.has_more,
        total=result.total,
        snapshot_at=result.snapshot_at,
    )
