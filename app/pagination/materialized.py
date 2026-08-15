"""Excepción explícita para colecciones que no provienen de una sola consulta."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from fastapi import Response

from app.pagination.http import HAS_MORE_HEADER, TOTAL_HEADER

T = TypeVar("T")


def paginate_materialized(
    items: Sequence[T],
    *,
    limit: int,
    offset: int,
    response: Response | None = None,
) -> list[T]:
    """Recorta solo colecciones acotadas o compuestas fuera de SQL."""
    total = len(items)
    selected = list(items[offset : offset + limit])
    if response is not None:
        response.headers[TOTAL_HEADER] = str(total)
        response.headers[HAS_MORE_HEADER] = str(offset + len(selected) < total).lower()
    return selected
