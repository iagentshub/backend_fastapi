from __future__ import annotations

from unittest.mock import patch

import pytest

from app.pagination.cursor import (
    cursor_context_signature,
    decode_cursor,
    decode_query_cursor,
    encode_cursor,
    encode_query_cursor,
)
from app.pagination.models import CursorParams, CursorPosition


def test_cursor_round_trip() -> None:
    position = CursorPosition(
        "2026-08-15T12:30:00Z",
        "item-42",
        snapshot_at="2026-08-15T12:31:00Z",
        total=42,
        page_number=3,
    )
    assert decode_cursor(encode_cursor(position)) == position


def test_signed_cursor_rejects_tampering() -> None:
    cursor = encode_cursor(CursorPosition("2026-08-15T12:30:00Z", "item-42"))
    payload, signature = cursor.split(".")
    replacement = "A" if payload[-1] != "A" else "B"
    with pytest.raises(ValueError, match="cursor inválido"):
        decode_cursor(f"{payload[:-1]}{replacement}.{signature}")


def test_signed_cursor_expires() -> None:
    with patch("app.pagination.cursor.time.time", return_value=1_000):
        cursor = encode_cursor(CursorPosition("2026-08-15T12:30:00Z", "item-42"))
    with patch("app.pagination.cursor.time.time", return_value=100_000):
        with pytest.raises(ValueError, match="cursor inválido"):
            decode_cursor(cursor)


def test_secret_rotation_invalidates_existing_cursor() -> None:
    with patch("app.pagination.cursor._secret", return_value="old-secret"):
        cursor = encode_cursor(CursorPosition("2026-08-15T12:30:00Z", "item-42"))
    with patch("app.pagination.cursor._secret", return_value="new-secret"):
        with pytest.raises(ValueError, match="cursor inválido"):
            decode_cursor(cursor)


def test_query_cursor_is_bound_to_its_query_context() -> None:
    position = CursorPosition("2026-08-29T12:00:00Z", "agent-1")
    context = cursor_context_signature({"user": "alice", "scope": "all"})
    cursor = encode_query_cursor(position, context=context)

    assert decode_query_cursor(cursor, context=context) == position
    with pytest.raises(ValueError, match="cursor inválido"):
        decode_query_cursor(cursor, context="another-query")


def test_cursor_params_reject_invalid_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        CursorParams(limit=0)


@pytest.mark.parametrize("value", ["", "***", "e30", "eyJ2IjoyfQ"])
def test_cursor_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="cursor inválido"):
        decode_cursor(value)
