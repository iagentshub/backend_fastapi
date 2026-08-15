from __future__ import annotations

import pytest

from app.pagination.cursor import decode_cursor, encode_cursor
from app.pagination.models import CursorPosition, OffsetPage, OffsetParams


def test_offset_params_reject_invalid_values() -> None:
    with pytest.raises(ValueError):
        OffsetParams(limit=0)
    with pytest.raises(ValueError):
        OffsetParams(limit=1, offset=-1)


def test_offset_page_reports_has_more() -> None:
    page = OffsetPage(items=[1, 2], total=5, params=OffsetParams(2, 2))
    assert page.has_more is True
    last = OffsetPage(items=[1], total=5, params=OffsetParams(2, 4))
    assert last.has_more is False


def test_cursor_round_trip() -> None:
    position = CursorPosition("2026-08-15T12:30:00Z", "item-42")
    assert decode_cursor(encode_cursor(position)) == position


@pytest.mark.parametrize("value", ["", "***", "e30", "eyJ2IjoyfQ"])
def test_cursor_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="cursor inválido"):
        decode_cursor(value)
