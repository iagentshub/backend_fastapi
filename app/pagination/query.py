"""Parámetros HTTP compartidos para listados keyset."""

from __future__ import annotations

from fastapi import Query, Request

from app.errors import APIError
from app.pagination.models import CursorParams


def scoped_cursor_params(
    request: Request,
    include_total: bool = False,
    consistent: bool = True,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, min_length=1, max_length=2048),
) -> CursorParams:
    """Construye una página cursor y rechaza el contrato offset retirado."""

    if "offset" in request.query_params:
        raise APIError(
            422,
            "invalid_field",
            "Offset ya no está disponible en este listado; utiliza cursor",
            extra={"field": "offset"},
        )
    return CursorParams(
        limit=limit,
        cursor=cursor,
        include_total=include_total,
        consistent=consistent,
    )
