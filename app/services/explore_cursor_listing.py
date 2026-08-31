"""Consulta cursor del catálogo público con filtros compartidos y orden estable."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from app.api.routes.explore._shared import STARRED_BY_REQUESTER, _add_owner_usernames
from app.config.content_languages import (
    CONTENT_LANGUAGE_SET,
    language_codes_from_labels,
)
from app.errors import APIError
from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.services.social_catalog import _PUBLIC_VAL, PUBLICLY_AVAILABLE_SQL
from app.storage.composite_cursor_page import (
    KeysetColumn,
    SnapshotColumn,
    fetch_composite_cursor_page,
)
from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage
from app.utils import now_iso

LINKED_BY_REQUESTER = (
    "EXISTS (SELECT 1 FROM resource_social mine "
    "WHERE mine.owner = ? "
    "AND mine.linked_to_user = resource_social.owner "
    "AND mine.linked_to_id = resource_social.resource_id "
    "AND mine.resource_type = resource_social.resource_type)"
)


@dataclass(frozen=True, slots=True)
class ExploreQuery:
    where: str
    params: tuple[Any, ...]
    base_where: str
    base_params: tuple[Any, ...]
    relation: str


def build_explore_query(
    *,
    username: str,
    type: str | None,
    category: str | None,
    q: str | None,
    tag: str | None,
    labels: Sequence[str] | None,
    languages: Sequence[str] | None,
    include_official: bool,
    pack_mode: bool | None,
    relation: str,
) -> ExploreQuery:
    if len(labels or ()) > 20:
        raise APIError(
            422,
            "invalid_field",
            "Demasiadas etiquetas para una sola búsqueda",
            extra={"field": "label"},
        )
    if len(languages or ()) > 20:
        raise APIError(
            422,
            "invalid_field",
            "Demasiados idiomas para una sola búsqueda",
            extra={"field": "language"},
        )
    if any(len(str(value)) > 100 for value in labels or ()):
        raise APIError(
            422,
            "invalid_field",
            "Etiqueta demasiado larga",
            extra={"field": "label"},
        )
    conditions = [
        "is_public = ?",
        "(owner != ? OR labels LIKE '%\"official\"%')",
        PUBLICLY_AVAILABLE_SQL,
    ]
    params: list[Any] = [_PUBLIC_VAL, username]
    if not include_official:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM resource_source_links source_link "
            "WHERE source_link.resource_type=resource_social.resource_type "
            "AND source_link.resource_id=resource_social.resource_id "
            "AND source_link.resource_owner_id=resource_social.owner)"
        )
    if pack_mode is True:
        conditions.append(
            "NOT (resource_type='knowledge' AND EXISTS ("
            "SELECT 1 FROM knowledge_items ki "
            "WHERE ki.id=resource_social.resource_id AND ki.pack_id IS NOT NULL))"
        )
    elif pack_mode is False:
        conditions.append("resource_type != 'knowledge_pack'")
    if type and type != "all":
        if type == "knowledge" and pack_mode is True:
            conditions.append("resource_type IN ('knowledge','knowledge_pack')")
        else:
            conditions.append("resource_type = ?")
            params.append(type)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if q:
        conditions.append("(name LIKE ? OR description LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if tag:
        conditions.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    normalized_languages = [
        str(value).strip().lower() for value in languages or () if str(value).strip()
    ]
    invalid_languages = [
        value for value in normalized_languages if value not in CONTENT_LANGUAGE_SET
    ]
    if invalid_languages:
        raise APIError(
            422,
            "invalid_field",
            "Idioma de contenido no soportado",
            extra={"field": "language", "invalid": invalid_languages},
        )
    if normalized_languages:
        conditions.append(
            "(" + " OR ".join(["labels LIKE ?"] * len(normalized_languages)) + ")"
        )
        params.extend(f'%"lang_{value}"%' for value in normalized_languages)
    if labels:
        conditions.append("(" + " OR ".join(["labels LIKE ?"] * len(labels)) + ")")
        params.extend(f'%"{value}"%' for value in labels)
    base_where = " AND ".join(conditions)
    base_params = tuple(params)
    if relation == "new":
        conditions.append(f"NOT {LINKED_BY_REQUESTER}")
        params.append(username)
    elif relation == "linked":
        conditions.append(LINKED_BY_REQUESTER)
        params.append(username)
    return ExploreQuery(
        where=" AND ".join(conditions),
        params=tuple(params),
        base_where=base_where,
        base_params=base_params,
        relation=relation,
    )


def _decode_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    for field, fallback in (("tags", []), ("labels", ["private"])):
        try:
            item[field] = json.loads(item.get(field) or json.dumps(fallback))
        except (ValueError, TypeError):
            item[field] = fallback
    item["languages"] = language_codes_from_labels(item["labels"])
    item["linked_by_me"] = bool(item.get("linked_by_me"))
    item["starred"] = bool(item.get("starred"))
    return item


async def list_explore_cursor(
    *,
    query: ExploreQuery,
    username: str,
    page: CursorParams,
    knowledge: KnowledgeStorage,
) -> tuple[CursorPage[dict[str, Any]], int]:
    context = cursor_context_signature(
        {
            "resource": "explore",
            "username": username,
            "where": query.where,
            "params": query.params,
            "consistent": page.consistent,
        }
    )
    columns = (
        KeysetColumn("updated_at", "updated_at"),
        KeysetColumn("stars_count", "stars_count"),
        KeysetColumn("resource_type", "resource_type", descending=False),
        KeysetColumn("resource_id", "resource_id", descending=False),
        KeysetColumn("owner", "owner", descending=False),
    )
    async with open_db() as conn:
        result = await fetch_composite_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM resource_social WHERE {query.where}",
            select_sql=(
                "SELECT resource_type,resource_id,owner,name,description,category,"
                "stars_count,linked_to_user,linked_to_id,trial_missing_deps,tags,"
                "labels,verified,updated_at,"
                f"{LINKED_BY_REQUESTER} AS linked_by_me,"
                f"{STARRED_BY_REQUESTER} AS starred "
                f"FROM resource_social WHERE {query.where}"
            ),
            params=query.params,
            select_params_prefix=(username, username),
            columns=columns,
            context=context,
            resource="explore",
            page=page,
            decode=_decode_row,
            snapshot=SnapshotColumn("updated_at", now_iso()),
        )
        linked_matches = 0
        if query.relation == "new" and page.cursor is None and not result.items:
            linked_matches = int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM resource_social "
                    f"WHERE {query.base_where} AND {LINKED_BY_REQUESTER}",
                    (*query.base_params, username),
                )
                or 0
            )
    items = list(result.items)
    pack_locations = await knowledge.pack_locations(
        [
            str(item.get("resource_id") or "")
            for item in items
            if item.get("resource_type") == "knowledge"
        ]
    )
    for item in items:
        location = pack_locations.get(str(item.get("resource_id") or ""))
        if location and item.get("resource_type") == "knowledge":
            item.update(location)
    await _add_owner_usernames(items)
    return CursorPage(
        items=items,
        next_cursor=result.next_cursor,
        has_more=result.has_more,
        total=result.total,
        snapshot_at=result.snapshot_at,
    ), linked_matches
