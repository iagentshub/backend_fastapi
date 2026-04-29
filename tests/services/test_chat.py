"""Tests del servicio de chat: routing de proveedores y manejo de errores."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.chat import stream_chat


def _make_agent(conn_type: str, model: str = "gpt-4o") -> dict:
    return {
        "id": "agent-test",
        "name": "Test",
        "system_prompt": "Eres un asistente.",
        "model": model,
        "temperature": 0.7,
        "skills": [],
        "use_memory": False,
    }


def _make_conn(conn_type: str, model: str = "gpt-4o") -> dict:
    return {
        "type": conn_type,
        "api_key": "fake-key",
        "model": model,
    }


def _skill_storage() -> MagicMock:
    sk = MagicMock()
    sk.get.return_value = None
    return sk


def _sse_done_response(reply: str = "Hola") -> MagicMock:
    """Simula una respuesta SSE de OpenAI con un chunk y [DONE]."""
    chunk = json.dumps({
        "choices": [{"delta": {"content": reply}}]
    }).encode()
    line_chunk = b"data: " + chunk + b"\n"
    line_done = b"data: [DONE]\n"
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = MagicMock(return_value=iter([line_chunk, line_done]))
    return mock_resp


@pytest.mark.parametrize("conn_type,expected_url", [
    ("openai", "https://api.openai.com/v1/chat/completions"),
    ("grok",   "https://api.x.ai/v1/chat/completions"),
    ("qwen",   "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
])
async def test_openai_compat_routing(conn_type, expected_url):
    """Verifica que cada provider OpenAI-compatible usa la URL correcta."""
    agent = _make_agent(conn_type)
    conn = _make_conn(conn_type)
    mock_resp = _sse_done_response("Hola")

    captured_url = []

    def fake_urlopen(req, timeout):
        captured_url.append(req.full_url)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        events = [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hola"}], _skill_storage())]

    assert captured_url[0] == expected_url
    assert any("done" in e for e in events)


async def test_stream_chat_returns_reply():
    agent = _make_agent("openai")
    conn = _make_conn("openai")
    mock_resp = _sse_done_response("Respuesta de prueba")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        events = [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hola"}], _skill_storage())]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["reply"] == "Respuesta de prueba"


async def test_stream_chat_connection_error_yields_error_event():
    agent = _make_agent("openai")
    conn = _make_conn("openai")

    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        events = [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hola"}], _skill_storage())]

    error_event = next((e for e in events if '"error"' in e), None)
    assert error_event is not None


async def test_stream_chat_unknown_provider_yields_error():
    agent = _make_agent("unknown_llm")
    conn = _make_conn("unknown_llm")

    events = [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hola"}], _skill_storage())]
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

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage())]

    messages = sent_payloads[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "Eres un chef." in messages[0]["content"]
