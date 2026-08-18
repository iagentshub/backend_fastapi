"""Tests del límite real de bytes recibidos por ASGI."""

from __future__ import annotations

import json
from collections import deque

import pytest

from app.middleware.body_limit import (
    UNLIMITED,
    BodySizeLimitMiddleware,
    configured_max_bytes,
    invalidate_body_limit_cache,
)


def _escribir_settings(data_dir, cfg: dict) -> None:
    """Escribe settings.json como lo haría el panel, invalidando el caché."""
    (data_dir / "settings.json").write_text(json.dumps(cfg), encoding="utf-8")
    invalidate_body_limit_cache()


def _scope(headers: list[tuple[bytes, bytes]] | None = None, path: str = "/test") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
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


async def _run_with_middleware(
    middleware: BodySizeLimitMiddleware,
    messages: list[dict],
    *,
    path: str = "/test",
) -> list[dict]:
    pending = deque(messages)
    sent: list[dict] = []

    async def receive() -> dict:
        return pending.popleft()

    async def send(message: dict) -> None:
        sent.append(message)

    await middleware(_scope(path=path), receive, send)
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


@pytest.mark.asyncio
async def test_sin_limite_deja_pasar_un_cuerpo_grande() -> None:
    """0 es el valor por defecto y significa «sin límite», no «nada pasa»."""
    middleware = BodySizeLimitMiddleware(_consume_body, max_bytes=UNLIMITED)
    sent = await _run_with_middleware(
        middleware,
        [{"type": "http.request", "body": b"x" * 5_000, "more_body": False}],
    )

    assert sent[0]["status"] == 204


def test_max_bytes_negativo_no_se_acepta() -> None:
    with pytest.raises(ValueError):
        BodySizeLimitMiddleware(_consume_body, max_bytes=-1)


@pytest.mark.asyncio
async def test_el_limite_lo_manda_la_config_del_admin(
    tmp_data_dir, patch_data_dir
) -> None:
    """Sin max_bytes fijo, el middleware relee lo que el admin dejó guardado.

    Fijarlo en el constructor lo congelaría en el valor del arranque: cambiar
    el número en el panel no haría nada hasta reiniciar el servidor.
    """
    _escribir_settings(tmp_data_dir, {"max_request_bytes": 4})
    middleware = BodySizeLimitMiddleware(_consume_body)

    sent = await _run_with_middleware(
        middleware,
        [{"type": "http.request", "body": b"x" * 10, "more_body": False}],
    )
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["detail"]["limit_bytes"] == 4

    _escribir_settings(tmp_data_dir, {"max_request_bytes": 0})
    sent = await _run_with_middleware(
        middleware,
        [{"type": "http.request", "body": b"x" * 10, "more_body": False}],
    )
    assert sent[0]["status"] == 204


def test_max_request_bytes_ilegible_cae_al_entorno(
    tmp_data_dir, patch_data_dir, monkeypatch
) -> None:
    """Un valor corrupto no puede dejar la puerta abierta en silencio."""
    import app.config.session as session_cfg

    monkeypatch.setattr(session_cfg, "BODY_MAX_BYTES", 7)
    _escribir_settings(tmp_data_dir, {"max_request_bytes": "grande"})

    assert configured_max_bytes() == 7
