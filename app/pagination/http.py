"""Adaptación HTTP central de páginas sin mezclarla con consultas SQL."""

from __future__ import annotations

from fastapi import Response

from app.pagination.models import CursorPage, OffsetPage

TOTAL_HEADER = "X-Total-Count"
NEXT_CURSOR_HEADER = "X-Next-Cursor"
HAS_MORE_HEADER = "X-Has-More"
# Cuántas filas dejó fuera el filtro de relación del catálogo. Vive aquí, con
# el resto de metadatos de página, porque lo que hay que recordar de una
# cabecera nueva es exponerla en CORS: la lista de abajo es la que lee
# `app/api/app.py`, y una cabecera declarada en otro sitio llegaría vacía a
# Flutter Web.
LINKED_HEADER = "X-Linked-Count"
PAGINATION_HEADERS = [
    TOTAL_HEADER,
    NEXT_CURSOR_HEADER,
    HAS_MORE_HEADER,
    LINKED_HEADER,
]

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
