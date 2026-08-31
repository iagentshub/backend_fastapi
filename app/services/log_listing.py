"""Filtros y paginación cursor del visor administrativo de logs."""

from __future__ import annotations

import time
from typing import Any

from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.storage.composite_cursor_page import (
    KeysetColumn,
    SnapshotColumn,
    fetch_composite_cursor_page,
)
from app.storage.db import open_db


def build_log_where(
    date_from: str | None,
    date_to: str | None,
    ip: str | None,
    username: str | None,
    level: str | None,
    source: str | None,
    category: str | None,
    action: str | None,
    resource_type: str | None,
    resource_id: str | None,
    outcome: str | None,
    q: str | None,
) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    filters = (
        (date_from, "date >= ?", date_from),
        (date_to, "date <= ?", date_to),
        (ip, "ip LIKE ?", f"%{ip}%"),
        (username, "username LIKE ?", f"%{username}%"),
        (level, "level = ?", level.upper() if level else None),
        (source, "source = ?", source.upper() if source else None),
        (category, "category = ?", category.upper() if category else None),
        (action, "action = ?", action.lower() if action else None),
        (
            resource_type,
            "resource_type = ?",
            resource_type.lower() if resource_type else None,
        ),
        (resource_id, "resource_id = ?", resource_id),
        (outcome, "outcome = ?", outcome.upper() if outcome else None),
        (q, "summary LIKE ?", f"%{q}%"),
    )
    for raw, clause, value in filters:
        if raw:
            clauses.append(clause)
            params.append(value)
    return "WHERE " + " AND ".join(clauses), params


async def list_logs_cursor(
    *, admin: str, where: str, params: list[Any], page: CursorParams
) -> CursorPage[dict[str, Any]]:
    context = cursor_context_signature(
        {"resource": "logs", "admin": admin, "where": where, "params": params}
    )
    async with open_db() as conn:
        return await fetch_composite_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM app_logs {where}",
            select_sql=(
                "SELECT id,ts,date,time,ip,username,level,source,summary,category,"
                "action,resource_type,resource_id,outcome,details_json "
                f"FROM app_logs {where}"
            ),
            params=tuple(params),
            columns=(
                KeysetColumn("ts", "ts"),
                KeysetColumn("id", "id"),
            ),
            context=context,
            resource="log",
            page=page,
            decode=lambda row: dict(row),
            snapshot=SnapshotColumn("ts", time.time(), decode=float),
        )
