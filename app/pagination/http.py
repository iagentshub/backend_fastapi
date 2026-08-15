"""Adaptación HTTP central de páginas sin mezclarla con consultas SQL."""

from __future__ import annotations

from fastapi import Response

from app.pagination.models import CursorPage, OffsetPage

TOTAL_HEADER = "X-Total-Count"
NEXT_CURSOR_HEADER = "X-Next-Cursor"
HAS_MORE_HEADER = "X-Has-More"
PAGINATION_HEADERS = [TOTAL_HEADER, NEXT_CURSOR_HEADER, HAS_MORE_HEADER]

def publish_offset_page(response: Response | None, page: OffsetPage[object]) -> None:
    if response is None:
        return
    response.headers[TOTAL_HEADER] = str(page.total)
    response.headers[HAS_MORE_HEADER] = str(page.has_more).lower()


def publish_cursor_page(response: Response | None, page: CursorPage[object]) -> None:
    if response is None:
        return
    response.headers[HAS_MORE_HEADER] = str(page.has_more).lower()
    if page.next_cursor is not None:
        response.headers[NEXT_CURSOR_HEADER] = page.next_cursor
