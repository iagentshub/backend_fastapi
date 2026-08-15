"""Codec estricto de cursores opacos para paginación keyset."""

from __future__ import annotations

import base64
import json
from binascii import Error as Base64Error

from app.pagination.models import CursorPosition

_CURSOR_VERSION = 1
_MAX_CURSOR_CHARS = 1024


def encode_cursor(position: CursorPosition) -> str:
    payload = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "created_at": position.created_at,
            "id": position.item_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> CursorPosition:
    """Decodifica un cursor sin aceptar campos vacíos ni versiones desconocidas."""

    if not value or len(value) > _MAX_CURSOR_CHARS:
        raise ValueError("cursor inválido")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (Base64Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("cursor inválido") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise ValueError("cursor inválido")
    created_at = payload.get("created_at")
    item_id = payload.get("id")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("cursor inválido")
    if not isinstance(item_id, str) or not item_id:
        raise ValueError("cursor inválido")
    return CursorPosition(created_at=created_at, item_id=item_id)
