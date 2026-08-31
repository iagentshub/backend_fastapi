"""Motor keyset para órdenes compuestos con direcciones mixtas."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence, TypeVar

from app.pagination.cursor import decode_query_cursor, encode_query_cursor
from app.pagination.metrics import observe_page
from app.pagination.models import CursorPage, CursorParams, CursorPosition
from app.pagination.total import exact_total
from app.storage.db import AsyncConn

T = TypeVar("T")
Scalar = str | int | float


@dataclass(frozen=True, slots=True)
class KeysetColumn:
    sql: str
    row_key: str
    descending: bool = True


@dataclass(frozen=True, slots=True)
class SnapshotColumn:
    sql: str
    initial_value: Scalar
    decode: Callable[[str], Scalar] = str


def _encode_position(values: Sequence[Scalar]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _decode_position(raw: str, expected: int) -> list[Scalar]:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("cursor inválido") from exc
    if (
        not isinstance(values, list)
        or len(values) != expected
        or any(not isinstance(value, (str, int, float)) for value in values)
    ):
        raise ValueError("cursor inválido")
    return values


def _after_predicate(
    columns: Sequence[KeysetColumn], values: Sequence[Scalar]
) -> tuple[str, list[Scalar]]:
    branches: list[str] = []
    params: list[Scalar] = []
    for index, column in enumerate(columns):
        terms: list[str] = []
        for previous in range(index):
            terms.append(f"{columns[previous].sql} = ?")
            params.append(values[previous])
        operator = "<" if column.descending else ">"
        terms.append(f"{column.sql} {operator} ?")
        params.append(values[index])
        branches.append("(" + " AND ".join(terms) + ")")
    return "(" + " OR ".join(branches) + ")", params


async def fetch_composite_cursor_page(
    conn: AsyncConn,
    *,
    count_sql: str,
    select_sql: str,
    params: tuple[Any, ...],
    columns: Sequence[KeysetColumn],
    context: str,
    resource: str,
    page: CursorParams,
    decode: Callable[[Any], T],
    snapshot: SnapshotColumn | None = None,
    select_params_prefix: tuple[Any, ...] = (),
) -> CursorPage[T]:
    """Ejecuta una página keyset sin imponer un orden temporal de dos columnas."""

    if not columns:
        raise ValueError("el orden keyset no puede estar vacío")
    started = time.perf_counter()
    position = (
        decode_query_cursor(page.cursor, context=context) if page.cursor else None
    )
    position_values = (
        _decode_position(position.created_at, len(columns)) if position else None
    )
    snapshot_raw = (
        position.snapshot_at
        if position is not None
        else (str(snapshot.initial_value) if snapshot and page.consistent else None)
    )
    if page.consistent and position is not None and snapshot and snapshot_raw is None:
        raise ValueError("cursor sin snapshot")

    filters: list[str] = []
    select_params: list[Any] = [*select_params_prefix, *params]
    if snapshot is not None and snapshot_raw is not None:
        filters.append(f"{snapshot.sql} <= ?")
        select_params.append(snapshot.decode(snapshot_raw))
    if position_values is not None:
        predicate, keyset_params = _after_predicate(columns, position_values)
        filters.append(predicate)
        select_params.extend(keyset_params)
    suffix = "".join(f" AND ({condition})" for condition in filters)

    total = None
    total_from_cursor = False
    carried_total = position.total if position else None
    if page.include_total:
        count_suffix = ""
        count_params: tuple[Any, ...] = params
        if snapshot is not None and snapshot_raw is not None:
            count_suffix = f" AND {snapshot.sql} <= ?"
            count_params = (*params, snapshot.decode(snapshot_raw))
        total, total_from_cursor = await exact_total(
            conn,
            sql=f"{count_sql}{count_suffix}",
            params=count_params,
            resource=resource,
            cursor_total=position.total if position else None,
        )

    order = ", ".join(
        f"{column.sql} {'DESC' if column.descending else 'ASC'}" for column in columns
    )
    rows = await conn.fetchall(
        f"{select_sql}{suffix} ORDER BY {order} LIMIT ?",
        (*select_params, page.limit + 1),
    )
    visible_rows = rows[: page.limit]
    has_more = len(rows) > page.limit
    next_cursor = None
    if has_more and visible_rows:
        last = visible_rows[-1]
        values = [last[column.row_key] for column in columns]
        next_cursor = encode_query_cursor(
            CursorPosition(
                _encode_position(values),
                str(values[-1]),
                snapshot_at=snapshot_raw,
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
        snapshot_at=snapshot_raw,
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
