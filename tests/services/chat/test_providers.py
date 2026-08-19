"""Routing entre proveedores, reintentos de red y errores de conexión."""

from __future__ import annotations

import json
import socket
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from app.services.chat import (
    _do_openai_stream_with_dns_retry,
    _openai_compat_chat_url,
    stream_chat,
)
from tests.services.chat._helpers import (
    _make_agent,
    _make_conn,
    _skill_storage,
    _sse_done_response,
)


def test_openai_stream_retries_transient_dns_failure():
    dns_error = urllib.error.URLError(
        socket.gaierror(-5, "No address associated with hostname")
    )
    response = _sse_done_response("OK")

    with (
        patch("app.services.chat.providers.safe_urlopen", side_effect=[dns_error, response]) as urlopen,
        patch("app.services.chat.providers.time.sleep") as sleep,
    ):
        reply, _, _ = _do_openai_stream_with_dns_retry(
            "https://example.com/v1/chat/completions",
            {"Authorization": "Bearer test"},
            {"model": "test", "messages": [], "stream": True},
            30,
        )

    assert reply == "OK"
    assert urlopen.call_count == 2
    sleep.assert_called_once_with(1)


def test_openai_stream_retries_transient_gateway_failure():
    gateway_error = urllib.error.HTTPError(
        "https://example.com/v1/chat/completions",
        504,
        "Gateway Timeout",
        {},
        BytesIO(b"gateway timeout"),
    )
    response = _sse_done_response("OK")

    with (
        patch(
            "app.services.chat.providers.safe_urlopen", side_effect=[gateway_error, response]
        ) as urlopen,
        patch("app.services.chat.providers.time.sleep") as sleep,
    ):
        reply, _, _ = _do_openai_stream_with_dns_retry(
            "https://example.com/v1/chat/completions",
            {"Authorization": "Bearer test"},
            {"model": "test", "messages": [], "stream": True},
            30,
        )

    assert reply == "OK"
    assert urlopen.call_count == 2
    sleep.assert_called_once_with(1)


def test_openai_stream_retries_timeout_before_first_token():
    response = _sse_done_response("OK")

    with (
        patch(
            "app.services.chat.providers.safe_urlopen",
            side_effect=[TimeoutError("The read operation timed out"), response],
        ) as urlopen,
        patch("app.services.chat.providers.time.sleep") as sleep,
    ):
        reply, _, _ = _do_openai_stream_with_dns_retry(
            "https://example.com/v1/chat/completions",
            {"Authorization": "Bearer test"},
            {"model": "test", "messages": [], "stream": True},
            30,
        )

    assert reply == "OK"
    assert urlopen.call_count == 2
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize(
    "configured,expected",
    [
        (
            "https://integrate.api.nvidia.com",
            "https://integrate.api.nvidia.com/v1/chat/completions",
        ),
        (
            "https://integrate.api.nvidia.com/v1",
            "https://integrate.api.nvidia.com/v1/chat/completions",
        ),
        (
            "https://integrate.api.nvidia.com/v1/models",
            "https://integrate.api.nvidia.com/v1/chat/completions",
        ),
        (
            "https://integrate.api.nvidia.com/v1/chat/completions/",
            "https://integrate.api.nvidia.com/v1/chat/completions",
        ),
        (
            "http://nim.internal:8000/v1",
            "http://nim.internal:8000/v1/chat/completions",
        ),
    ],
)
def test_nvidia_connection_url_accepts_base_or_complete_endpoint(configured, expected):
    assert _openai_compat_chat_url("nvidia", configured) == expected


@pytest.mark.parametrize(
    "conn_type,expected_url",
    [
        ("openai", "https://api.openai.com/v1/chat/completions"),
        ("grok", "https://api.x.ai/v1/chat/completions"),
        (
            "qwen",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ),
    ],
)
async def test_openai_compat_routing(conn_type, expected_url):
    """Verifica que cada provider OpenAI-compatible usa la URL correcta."""
    agent = _make_agent(conn_type)
    conn = _make_conn(conn_type)
    mock_resp = _sse_done_response("Hola")

    captured_url = []

    def fake_urlopen(req, timeout):
        captured_url.append(req.full_url)
        return mock_resp

    with patch("app.services.chat.providers.safe_urlopen", side_effect=fake_urlopen):
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hola"}], _skill_storage()
            )
        ]

    assert captured_url[0] == expected_url
    assert any("done" in e for e in events)


async def test_stream_chat_returns_reply():
    agent = _make_agent("openai")
    conn = _make_conn("openai")
    mock_resp = _sse_done_response("Respuesta de prueba")

    with patch("app.services.chat.providers.safe_urlopen", return_value=mock_resp):
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hola"}], _skill_storage()
            )
        ]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["reply"] == "Respuesta de prueba"


async def test_stream_chat_connection_error_yields_error_event():
    agent = _make_agent("openai")
    conn = _make_conn("openai")

    with patch("app.services.chat.providers.safe_urlopen", side_effect=Exception("network error")):
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hola"}], _skill_storage()
            )
        ]

    error_event = next((e for e in events if '"error"' in e), None)
    assert error_event is not None


async def test_stream_chat_explains_openai_compatible_404():
    agent = _make_agent("nvidia", model="vendor/chat-model")
    conn = {
        **_make_conn("nvidia", model="vendor/chat-model"),
        "url": "https://integrate.api.nvidia.com/v1",
    }
    not_found = urllib.error.HTTPError(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        404,
        "Not Found",
        {},
        BytesIO(b"404 page not found"),
    )

    with patch("app.services.chat.providers.safe_urlopen", side_effect=not_found):
        events = [
            event
            async for event in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
            )
        ]

    error_event = next(event for event in events if '"type": "error"' in event)
    assert "vendor/chat-model" in error_event
    assert "https://integrate.api.nvidia.com/v1/chat/completions" in error_event
    assert "OpenAI-compatible" in error_event


async def test_stream_chat_unknown_provider_yields_error():
    agent = _make_agent("unknown_llm")
    conn = _make_conn("unknown_llm")

    events = [
        e
        async for e in stream_chat(
            agent, conn, [{"role": "user", "content": "Hola"}], _skill_storage()
        )
    ]
    error_event = next((e for e in events if '"error"' in e), None)
    assert error_event is not None


async def test_system_prompt_included_in_messages():
    agent = _make_agent("openai")
    agent["system_prompt"] = "Eres un chef."
    conn = _make_conn("openai")

    sent_payloads = []

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode())
        sent_payloads.append(body)
        return _sse_done_response()

    with patch("app.services.chat.providers.safe_urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    messages = sent_payloads[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "Eres un chef." in messages[0]["content"]
