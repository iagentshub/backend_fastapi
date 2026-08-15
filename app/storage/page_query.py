"""Primitivas SQL reutilizables para no materializar colecciones completas."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from app.pagination.models import OffsetPage, OffsetParams
from app.storage.db import AsyncConn

T = TypeVar("T")


async def fetch_offset_page(
    conn: AsyncConn,
    *,
    count_sql: str,
    select_sql: str,
    params: tuple[Any, ...],
    page: OffsetParams,
    decode: Callable[[Any], T],
) -> OffsetPage[T]:
    """Ejecuta COUNT y SELECT limitado con exactamente los mismos parámetros base.

    ``select_sql`` debe incluir un ORDER BY determinista. Los únicos fragmentos
    añadidos aquí son LIMIT/OFFSET parametrizados, por lo que los valores del
    usuario nunca se interpolan en SQL.
    """

    total = int(await conn.fetchval(count_sql, params) or 0)
    rows = await conn.fetchall(
        f"{select_sql} LIMIT ? OFFSET ?",
        (*params, page.limit, page.offset),
    )
    return OffsetPage(
        items=[decode(row) for row in rows],
        total=total,
        params=page,
    )
