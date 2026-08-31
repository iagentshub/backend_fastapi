"""Consulta SQL keyset reutilizable para listados ordenados por dos columnas."""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

from app.pagination.cursor import decode_query_cursor, encode_query_cursor
from app.pagination.metrics import observe_page
from app.pagination.models import CursorPage, CursorParams, CursorPosition
from app.pagination.total import exact_total
from app.storage.db import AsyncConn
from app.utils import now_iso

T = TypeVar("T")


async def fetch_cursor_page(
    conn: AsyncConn,
    *,
    count_sql: str,
    select_sql: str,
    params: tuple[Any, ...],
    position_column: str,
    id_column: str,
    context: str,
    resource: str,
    page: CursorParams,
    decode: Callable[[Any], T],
) -> CursorPage[T]:
    """Ejecuta LIMIT+1 mediante keyset y cuenta solo bajo petición."""

    started = time.perf_counter()
    position = decode_query_cursor(page.cursor, context=context) if page.cursor else None
    snapshot_at = (
        position.snapshot_at
        if position is not None
        else (now_iso() if page.consistent else None)
    )
    if page.consistent and position is not None and snapshot_at is None:
        raise ValueError("cursor sin snapshot")
    select_params = list(params)
    cursor_filter = ""
    if snapshot_at is not None:
        cursor_filter += f" AND {position_column} <= ?"
        select_params.append(snapshot_at)
    if position is not None:
        cursor_filter += f" AND ({position_column}, {id_column}) < (?, ?)"
        select_params.extend([position.created_at, position.item_id])
    total = None
    total_from_cursor = False
    carried_total = position.total if position else None
    if page.include_total:
        count_filter = f" AND {position_column} <= ?" if snapshot_at else ""
        count_params = (*params, snapshot_at) if snapshot_at else params
        total, total_from_cursor = await exact_total(
            conn,
            sql=f"{count_sql}{count_filter}",
            params=count_params,
            resource=resource,
            cursor_total=position.total if position else None,
        )
    rows = await conn.fetchall(
        f"{select_sql}{cursor_filter} "
        f"ORDER BY {position_column} DESC, {id_column} DESC LIMIT ?",
        (*select_params, page.limit + 1),
    )
    visible_rows = rows[: page.limit]
    has_more = len(rows) > page.limit
    next_cursor = None
    if has_more and visible_rows:
        last = visible_rows[-1]
        next_cursor = encode_query_cursor(
            CursorPosition(
                str(last[position_column.rsplit('.', 1)[-1]]),
                str(last[id_column.rsplit('.', 1)[-1]]),
                snapshot_at=snapshot_at,
                total=total if total is not None else carried_total,
                page_number=(position.page_number + 1 if position else 2),
            ),
            context=context,
        )
    result = CursorPage(
        items=[decode(row) for row in visible_rows],
        next_cursor=next_cursor,
        has_more=has_more,
        total=total,
        snapshot_at=snapshot_at,
    )
    observe_page(
        resource,
        duration_ms=(time.perf_counter() - started) * 1000,
        items=len(result.items),
        has_more=result.has_more,
        include_total=page.include_total,
        total_from_cursor=total_from_cursor,
        page_number=position.page_number if position else 1,
    )
    return result
