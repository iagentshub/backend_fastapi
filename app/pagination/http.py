"""Adaptación HTTP central de páginas sin mezclarla con consultas SQL."""

from __future__ import annotations

from fastapi import Response

from app.pagination.models import CursorPage

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
