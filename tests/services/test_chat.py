"""Tests del servicio de chat: routing de proveedores y manejo de errores."""

from __future__ import annotations

import json
import socket
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.services.chat import (
    _do_openai_stream_with_dns_retry,
    _openai_compat_chat_url,
    stream_chat,
)


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
    chunk = json.dumps({"choices": [{"delta": {"content": reply}}]}).encode()
    line_chunk = b"data: " + chunk + b"\n"
    line_done = b"data: [DONE]\n"
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = MagicMock(return_value=iter([line_chunk, line_done]))
    return mock_resp


def test_openai_stream_retries_transient_dns_failure():
    dns_error = urllib.error.URLError(
        socket.gaierror(-5, "No address associated with hostname")
    )
    response = _sse_done_response("OK")

    with (
        patch("urllib.request.urlopen", side_effect=[dns_error, response]) as urlopen,
        patch("app.services.chat.time.sleep") as sleep,
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
            "urllib.request.urlopen", side_effect=[gateway_error, response]
        ) as urlopen,
        patch("app.services.chat.time.sleep") as sleep,
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
            "urllib.request.urlopen",
            side_effect=[TimeoutError("The read operation timed out"), response],
        ) as urlopen,
        patch("app.services.chat.time.sleep") as sleep,
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

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
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

    with patch("urllib.request.urlopen", return_value=mock_resp):
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

    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
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

    with patch("urllib.request.urlopen", side_effect=not_found):
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

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    messages = sent_payloads[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "Eres un chef." in messages[0]["content"]


# ─── Tests de recuerdo de conversaciones anteriores ────────────────────────────


def _chat_storage_mock(convs: list, messages_by_conv: dict) -> MagicMock:
    storage = MagicMock()

    async def _list_conversations(user_id, agent_id, limit=50):
        return convs

    async def _get_messages(conv_id, user_id, limit=200):
        return messages_by_conv.get(conv_id, [])

    storage.list_conversations = _list_conversations
    storage.get_messages = _get_messages
    return storage


async def _sent_system_message(
    agent, conn, history, chat_storage, user_id, conversation_id
):
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                history,
                _skill_storage(),
                None,
                None,
                chat_storage,
                user_id,
                conversation_id,
            )
        ]
    return sent_payloads[0]["messages"][0]["content"]


async def test_history_injected_from_past_conversations():
    agent = _make_agent("openai")
    agent["use_memory"] = True
    conn = _make_conn("openai")
    convs = [{"id": "conv-old"}]
    messages = {
        "conv-old": [
            {"role": "user", "content": "Me llamo Ana."},
            {"role": "assistant", "content": "Encantado, Ana."},
        ]
    }
    chat_storage = _chat_storage_mock(convs, messages)

    system_message = await _sent_system_message(
        agent,
        conn,
        [{"role": "user", "content": "¿Cómo me llamo?"}],
        chat_storage,
        "user-1",
        "conv-current",
    )

    assert "Ana" in system_message


async def test_history_excludes_current_conversation():
    agent = _make_agent("openai")
    agent["use_memory"] = True
    conn = _make_conn("openai")
    convs = [{"id": "conv-current"}]
    messages = {"conv-current": [{"role": "user", "content": "No debería aparecer"}]}
    chat_storage = _chat_storage_mock(convs, messages)

    system_message = await _sent_system_message(
        agent,
        conn,
        [{"role": "user", "content": "Hola"}],
        chat_storage,
        "user-1",
        "conv-current",
    )

    assert "No debería aparecer" not in system_message


async def test_history_not_injected_when_use_memory_disabled():
    agent = _make_agent("openai")
    agent["use_memory"] = False
    conn = _make_conn("openai")
    convs = [{"id": "conv-old"}]
    messages = {"conv-old": [{"role": "user", "content": "Dato pasado"}]}
    chat_storage = _chat_storage_mock(convs, messages)

    system_message = await _sent_system_message(
        agent,
        conn,
        [{"role": "user", "content": "Hola"}],
        chat_storage,
        "user-1",
        "conv-current",
    )

    assert "Dato pasado" not in system_message


async def test_history_not_queried_without_user_id():
    """Sin user_id (p.ej. invitados) no debe consultarse el historial."""
    agent = _make_agent("openai")
    agent["use_memory"] = True
    conn = _make_conn("openai")

    chat_storage = MagicMock()
    chat_storage.list_conversations = MagicMock(
        side_effect=AssertionError("no debería llamarse sin user_id")
    )

    with patch("urllib.request.urlopen", return_value=_sse_done_response()):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                None,
                None,
                chat_storage,
                None,
                None,
            )
        ]


async def test_effort_level_is_sent_to_openai_compatible_provider():
    agent = _make_agent("openai")
    agent["effort_level"] = "high"
    conn = _make_conn("openai")
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [
            event
            async for event in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    assert sent_payloads[0]["reasoning_effort"] == "high"


# ─── Tests de token tracking ───────────────────────────────────────────────────


def _sse_openai_with_usage(
    reply: str = "Hi", prompt_tokens: int = 10, completion_tokens: int = 5
) -> MagicMock:
    chunk = json.dumps({"choices": [{"delta": {"content": reply}}]}).encode()
    # choices vacío es el formato real que manda OpenAI/NVIDIA en el chunk de usage
    usage_chunk = json.dumps(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }
    ).encode()
    line_chunk = b"data: " + chunk + b"\n"
    line_usage = b"data: " + usage_chunk + b"\n"
    line_done = b"data: [DONE]\n"
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = MagicMock(
        return_value=iter([line_chunk, line_usage, line_done])
    )
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
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    assert sent_payloads[0].get("stream_options") == {"include_usage": True}
    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"] == {"in": 10, "out": 5}


def _sse_claude_response(
    reply: str = "Hi", input_tokens: int = 20, output_tokens: int = 8
) -> MagicMock:
    start = json.dumps(
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": input_tokens}},
        }
    ).encode()
    delta_text = json.dumps(
        {
            "type": "content_block_delta",
            "delta": {"text": reply},
        }
    ).encode()
    msg_delta = json.dumps(
        {
            "type": "message_delta",
            "usage": {"output_tokens": output_tokens},
        }
    ).encode()
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
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"] == {"in": 20, "out": 8}


def _ollama_response(
    reply: str = "Hi", prompt_eval_count: int = 15, eval_count: int = 6
) -> MagicMock:
    body = json.dumps(
        {
            "message": {"content": reply},
            "prompt_eval_count": prompt_eval_count,
            "eval_count": eval_count,
        }
    ).encode()
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
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"] == {"in": 15, "out": 6}


async def test_openai_usage_chunk_with_empty_choices():
    """El chunk de usage llega con choices:[] — no debe lanzar IndexError ni perder los tokens."""
    agent = _make_agent("nvidia", model="meta/llama-3.1-8b-instruct")
    conn = _make_conn("nvidia", model="meta/llama-3.1-8b-instruct")
    mock_resp = _sse_openai_with_usage("Hola", prompt_tokens=30, completion_tokens=12)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"]["in"] == 30
    assert data["tokens"]["out"] == 12


@pytest.mark.parametrize(
    ("model", "expected_options"),
    [
        ("deepseek-ai/deepseek-v4-pro", {"thinking": False}),
        (
            "deepseek-ai/deepseek-v4-flash",
            {"thinking": False, "reasoning_effort": "none"},
        ),
    ],
)
async def test_deepseek_v4_uses_bounded_non_thinking_generation(
    model, expected_options
):
    agent = _make_agent("nvidia", model=model)
    conn = _make_conn("nvidia", model=model)
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [
            event
            async for event in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    assert sent_payloads[0]["max_tokens"] == 2_048
    assert sent_payloads[0]["chat_template_kwargs"] == expected_options


# ─── Tests de truncado de contexto ────────────────────────────────────────────


async def test_truncate_history_no_op():
    from app.services.chat import _truncate_history

    h = [{"role": "user", "content": "hola"}]
    assert _truncate_history(h, system_tokens=100) == h


async def test_truncate_history_descarta_antiguos():
    from app.services.chat import _truncate_history

    msgs = [{"role": "user", "content": "x" * 1000}] * 20  # ~5000 tokens
    result = _truncate_history(msgs, system_tokens=0, max_context=2000)
    assert len(result) < 20
    assert len(result) >= 2


async def test_estimate_tokens():
    from app.services.chat import _estimate_tokens

    assert _estimate_tokens("hola") == 1
    assert _estimate_tokens("a" * 400) == 100


# ─── Tests de inyección de prompts vía mención "@alias" ────────────────────────


def _prompt_storage_mock(prompts_by_id: dict) -> MagicMock:
    storage = MagicMock()

    async def _get_any(prompt_id, owner_id=None):
        return prompts_by_id.get(prompt_id)

    storage.get_any = _get_any
    return storage


async def test_prompt_mention_injected_into_system():
    agent = _make_agent("openai")
    agent["prompts"] = ["prompt-1"]
    conn = _make_conn("openai")
    prompt_storage = _prompt_storage_mock(
        {
            "prompt-1": {
                "id": "prompt-1",
                "alias": "resumen",
                "name": "Resumen",
                "content": "Resume el texto en 3 frases.",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola @resumen por favor"}],
                _skill_storage(),
                prompt_storage=prompt_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "Resume el texto en 3 frases." in system_message


async def test_prompt_mention_is_case_insensitive():
    agent = _make_agent("openai")
    agent["prompts"] = ["prompt-1"]
    conn = _make_conn("openai")
    prompt_storage = _prompt_storage_mock(
        {
            "prompt-1": {
                "id": "prompt-1",
                "alias": "resumen",
                "name": "Resumen",
                "content": "Resume el texto en 3 frases.",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola @RESUMEN por favor"}],
                _skill_storage(),
                prompt_storage=prompt_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "Resume el texto en 3 frases." in system_message


async def test_no_mention_does_not_inject_prompt():
    agent = _make_agent("openai")
    agent["prompts"] = ["prompt-1"]
    conn = _make_conn("openai")
    prompt_storage = _prompt_storage_mock(
        {
            "prompt-1": {
                "id": "prompt-1",
                "alias": "resumen",
                "name": "Resumen",
                "content": "Resume el texto en 3 frases.",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola, sin menciones"}],
                _skill_storage(),
                prompt_storage=prompt_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "Resume el texto en 3 frases." not in system_message


async def test_mention_of_prompt_not_in_agent_catalog_is_ignored():
    """El alias mencionado debe resolverse solo contra agent.prompts, no
    contra cualquier prompt existente en la BD."""
    agent = _make_agent("openai")
    agent["prompts"] = []  # el agente no tiene ningún prompt vinculado
    conn = _make_conn("openai")
    prompt_storage = _prompt_storage_mock(
        {
            "prompt-1": {
                "id": "prompt-1",
                "alias": "resumen",
                "name": "Resumen",
                "content": "Resume el texto en 3 frases.",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola @resumen"}],
                _skill_storage(),
                prompt_storage=prompt_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "Resume el texto en 3 frases." not in system_message


async def test_prompt_storage_none_does_not_break_stream_chat():
    """Rama guest (prompt_storage=None): no debe intentar resolver menciones."""
    agent = _make_agent("openai")
    agent["prompts"] = ["prompt-1"]
    conn = _make_conn("openai")

    with patch("urllib.request.urlopen", return_value=_sse_done_response()):
        events = [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola @resumen"}],
                _skill_storage(),
            )
        ]

    assert any("done" in e for e in events)
