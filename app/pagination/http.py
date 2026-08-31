"""Adaptación HTTP central de páginas sin mezclarla con consultas SQL."""

from __future__ import annotations

from fastapi import Response

from app.errors import APIError
from app.pagination.metrics import increment
from app.pagination.models import CursorPage
from app.pagination.total import ExactTotalTimeout

NEXT_CURSOR_HEADER = "X-Next-Cursor"
HAS_MORE_HEADER = "X-Has-More"
PAGINATION_HEADERS = [
    NEXT_CURSOR_HEADER,
    HAS_MORE_HEADER,
]


def publish_cursor_page(response: Response | None, page: CursorPage[object]) -> None:
    if response is None:
        return
    response.headers[HAS_MORE_HEADER] = str(page.has_more).lower()
    if page.next_cursor is not None:
        response.headers[NEXT_CURSOR_HEADER] = page.next_cursor


def cursor_error(exc: Exception, *, resource: str) -> APIError:
    """Traduce al contrato v2 los dos finales que puede tener una página."""
    if isinstance(exc, ExactTotalTimeout):
        return APIError(
            503,
            "pagination_total_timeout",
            "El total exacto no estuvo disponible dentro del tiempo permitido",
            extra={"resource": resource},
        )
    increment(resource, "invalid_cursors")
    return APIError(422, "invalid_cursor", "Cursor no válido")
