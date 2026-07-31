"""Tests del límite real de bytes recibidos por ASGI."""

from __future__ import annotations

import json
from collections import deque

import pytest

from app.middleware.body_limit import BodySizeLimitMiddleware


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/test",
        "raw_path": b"/test",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }


async def _consume_body(scope, receive, send) -> None:
    while True:
        message = await receive()
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _run(
    messages: list[dict],
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[dict]:
    pending = deque(messages)
    sent: list[dict] = []

    async def receive() -> dict:
        return pending.popleft()

    async def send(message: dict) -> None:
        sent.append(message)

    middleware = BodySizeLimitMiddleware(_consume_body, max_bytes=5)
    await middleware(_scope(headers), receive, send)
    return sent


@pytest.mark.asyncio
async def test_rejects_declared_content_length_before_reading_body() -> None:
    sent = await _run(
        [{"type": "http.request", "body": b"", "more_body": False}],
        headers=[(b"content-length", b"6")],
    )

    assert sent[0]["status"] == 413
    payload = json.loads(sent[1]["body"])
    assert payload["detail"] == {
        "code": "payload_too_large",
        "message": "Payload demasiado grande",
        "limit_bytes": 5,
    }


@pytest.mark.asyncio
async def test_rejects_chunked_body_using_bytes_actually_received() -> None:
    sent = await _run(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ],
    )

    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_allows_body_at_exact_limit() -> None:
    sent = await _run(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ],
    )

    assert sent[0]["status"] == 204
