"""Codec firmado de cursores opacos para paginación keyset."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from binascii import Error as Base64Error
from typing import Any

from app.auth.passwords import _secret
from app.config.pagination import CURSOR_TTL_SECONDS
from app.pagination.models import CursorPosition

_CURSOR_VERSION = 2
_MAX_CURSOR_CHARS = 2048
_SIGNING_CONTEXT = b"iagentshub:pagination:cursor:v2"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _signing_key() -> bytes:
    return hmac.new(
        _secret().encode("utf-8"), _SIGNING_CONTEXT, hashlib.sha256
    ).digest()


def _encode(position: CursorPosition, *, kind: str, context: str) -> str:
    now = int(time.time())
    payload = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "kind": kind,
            "position": position.created_at,
            "id": position.item_id,
            "context": context,
            "snapshot_at": position.snapshot_at,
            "total": position.total,
            "page": position.page_number,
            "iat": now,
            "exp": now + CURSOR_TTL_SECONDS,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_payload = _b64encode(payload)
    signature = hmac.new(
        _signing_key(), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded_payload}.{_b64encode(signature)}"


def _decode(value: str, *, kind: str, context: str) -> CursorPosition:
    if not value or len(value) > _MAX_CURSOR_CHARS or value.count(".") != 1:
        raise ValueError("cursor inválido")
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        expected = hmac.new(
            _signing_key(), encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(encoded_signature)):
            raise ValueError("cursor inválido")
        payload: Any = json.loads(_b64decode(encoded_payload).decode("utf-8"))
    except (
        Base64Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise ValueError("cursor inválido") from exc
    now = int(time.time())
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or payload.get("kind") != kind
        or payload.get("context") != context
        or not isinstance(payload.get("iat"), int)
        or not isinstance(payload.get("exp"), int)
        or payload["iat"] > now + 60
        or payload["exp"] < now
    ):
        raise ValueError("cursor inválido")
    position = payload.get("position")
    item_id = payload.get("id")
    snapshot_at = payload.get("snapshot_at")
    total = payload.get("total")
    page_number = payload.get("page")
    if not isinstance(position, str) or not position:
        raise ValueError("cursor inválido")
    if not isinstance(item_id, str) or not item_id:
        raise ValueError("cursor inválido")
    if snapshot_at is not None and not isinstance(snapshot_at, str):
        raise ValueError("cursor inválido")
    if total is not None and (not isinstance(total, int) or total < 0):
        raise ValueError("cursor inválido")
    if not isinstance(page_number, int) or page_number < 1:
        raise ValueError("cursor inválido")
    return CursorPosition(
        created_at=position,
        item_id=item_id,
        snapshot_at=snapshot_at,
        total=total,
        page_number=page_number,
    )


def encode_cursor(position: CursorPosition) -> str:
    return _encode(position, kind="chat", context="")


def decode_cursor(value: str) -> CursorPosition:
    return _decode(value, kind="chat", context="")


def cursor_context_signature(parts: object) -> str:
    """Huella estable que vincula un cursor a usuario, recurso y filtros."""

    canonical = json.dumps(
        parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def encode_query_cursor(position: CursorPosition, *, context: str) -> str:
    return _encode(position, kind="query", context=context)


def decode_query_cursor(value: str, *, context: str) -> CursorPosition:
    return _decode(value, kind="query", context=context)
