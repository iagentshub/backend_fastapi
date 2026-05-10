"""Tests del servicio de chat: routing de proveedores y manejo de errores."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.chat import auto_update_memory, stream_chat


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


# ─── Tests de auto_update_memory ──────────────────────────────────────────────

def _make_memory_storage(existing: str = "") -> MagicMock:
    storage = MagicMock()
    storage.get.return_value = existing or None
    return storage


async def test_auto_update_memory_saves_to_storage():
    """auto_update_memory debe guardar el contenido devuelto por el LLM en el storage."""
    agent = _make_agent("openai")
    agent["id"] = "mi-agente"
    conn = _make_conn("openai")
    history = [{"role": "user", "content": "Me llamo Ana."}]
    reply = "Encantado, Ana."
    mem_storage = _make_memory_storage()

    mock_resp = _sse_done_response("- El usuario se llama Ana.")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        await auto_update_memory(agent, conn, history, reply, mem_storage)

    mem_storage.save.assert_called_once()
    filename, content = mem_storage.save.call_args[0]
    assert filename == "mi-agente.md"
    assert "Ana" in content


async def test_auto_update_memory_uses_custom_memory_file():
    """Si el agente tiene memory_file configurado, se usa ese nombre."""
    agent = _make_agent("openai")
    agent["id"] = "mi-agente"
    agent["memory_file"] = "proyecto-x.md"
    conn = _make_conn("openai")
    mem_storage = _make_memory_storage()

    mock_resp = _sse_done_response("- Proyecto X en marcha.")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        await auto_update_memory(agent, conn, [], "ok", mem_storage)

    filename, _ = mem_storage.save.call_args[0]
    assert filename == "proyecto-x.md"


async def test_auto_update_memory_does_not_raise_on_llm_error():
    """Si el LLM falla, auto_update_memory no debe propagar la excepción."""
    agent = _make_agent("openai")
    conn = _make_conn("openai")
    mem_storage = _make_memory_storage()

    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        # No debe lanzar excepción
        await auto_update_memory(agent, conn, [], "respuesta", mem_storage)

    mem_storage.save.assert_not_called()


async def test_auto_update_memory_does_not_save_empty_content():
    """Si el LLM devuelve contenido vacío, no se debe guardar nada."""
    agent = _make_agent("openai")
    conn = _make_conn("openai")
    mem_storage = _make_memory_storage()

    mock_resp = _sse_done_response("")  # reply vacío

    with patch("urllib.request.urlopen", return_value=mock_resp):
        await auto_update_memory(agent, conn, [], "ok", mem_storage)

    mem_storage.save.assert_not_called()


# ─── Tests de token tracking ───────────────────────────────────────────────────

def _sse_openai_with_usage(reply: str = "Hi", prompt_tokens: int = 10, completion_tokens: int = 5) -> MagicMock:
    chunk = json.dumps({"choices": [{"delta": {"content": reply}}]}).encode()
    # choices vacío es el formato real que manda OpenAI/NVIDIA en el chunk de usage
    usage_chunk = json.dumps({
        "choices": [],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }).encode()
    line_chunk = b"data: " + chunk + b"\n"
    line_usage = b"data: " + usage_chunk + b"\n"
    line_done = b"data: [DONE]\n"
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = MagicMock(return_value=iter([line_chunk, line_usage, line_done]))
    return mock_resp


async def test_openai_done_event_includes_tokens():
    agent = _make_agent("openai")
    conn = _make_conn("openai")
    mock_resp = _sse_openai_with_usage("Hello", prompt_tokens=10, completion_tokens=5)

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        events = [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage())]

    assert sent_payloads[0].get("stream_options") == {"include_usage": True}
    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"] == {"in": 10, "out": 5}


def _sse_claude_response(reply: str = "Hi", input_tokens: int = 20, output_tokens: int = 8) -> MagicMock:
    start = json.dumps({
        "type": "message_start",
        "message": {"usage": {"input_tokens": input_tokens}},
    }).encode()
    delta_text = json.dumps({
        "type": "content_block_delta",
        "delta": {"text": reply},
    }).encode()
    msg_delta = json.dumps({
        "type": "message_delta",
        "usage": {"output_tokens": output_tokens},
    }).encode()
    lines = [
        b"data: " + start + b"\n",
        b"data: " + delta_text + b"\n",
        b"data: " + msg_delta + b"\n",
    ]
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = MagicMock(return_value=iter(lines))
    return mock_resp


async def test_claude_done_event_includes_tokens():
    agent = _make_agent("claude", model="claude-3-5-sonnet-20241022")
    conn = _make_conn("claude", model="claude-3-5-sonnet-20241022")
    mock_resp = _sse_claude_response("Bonjour", input_tokens=20, output_tokens=8)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        events = [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage())]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"] == {"in": 20, "out": 8}


def _ollama_response(reply: str = "Hi", prompt_eval_count: int = 15, eval_count: int = 6) -> MagicMock:
    body = json.dumps({
        "message": {"content": reply},
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
    }).encode()
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = body
    return mock_resp


async def test_ollama_done_event_includes_tokens():
    agent = _make_agent("ollama", model="llama3")
    conn = {**_make_conn("ollama", model="llama3"), "host": "http://localhost:11434"}
    mock_resp = _ollama_response("Ciao", prompt_eval_count=15, eval_count=6)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        events = [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage())]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"] == {"in": 15, "out": 6}


async def test_openai_usage_chunk_with_empty_choices():
    """El chunk de usage llega con choices:[] — no debe lanzar IndexError ni perder los tokens."""
    agent = _make_agent("nvidia", model="meta/llama-3.1-8b-instruct")
    conn = _make_conn("nvidia", model="meta/llama-3.1-8b-instruct")
    mock_resp = _sse_openai_with_usage("Hola", prompt_tokens=30, completion_tokens=12)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        events = [e async for e in stream_chat(agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage())]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"]["in"] == 30
    assert data["tokens"]["out"] == 12
