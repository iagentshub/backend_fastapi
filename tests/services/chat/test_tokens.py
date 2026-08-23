"""Contabilidad de tokens y streaming incremental por proveedor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connections.anthropic import _stream as _do_claude_stream
from app.services.chat import stream_chat
from app.services.llm_executor import LLMCapacityError
from tests.services.chat._helpers import (
    _make_agent,
    _make_conn,
    _skill_storage,
    _sse_done_response,
)

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

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
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


async def test_capacity_exhausted_emite_error_sse_controlado():
    agent = _make_agent("openai")
    conn = _make_conn("openai")

    with patch(
        "app.services.chat._streaming.run_llm_blocking",
        new=AsyncMock(side_effect=LLMCapacityError("sin cupo")),
    ):
        events = [
            event
            async for event in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    error = next(event for event in events if '"type": "error"' in event)
    data = json.loads(error.removeprefix("data: ").strip())
    assert data["code"] == "llm_capacity_exceeded"
    assert "máximo de conversaciones" in data["message"]


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


def test_claude_invalid_event_warns_and_continues():
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = MagicMock(
        return_value=iter(
            [
                b"data: no-es-json\n",
                b'data: {"type":"content_block_delta","delta":{"text":"ok"}}\n',
            ]
        )
    )

    with (
        patch("app.connections.anthropic.safe_urlopen", return_value=mock_resp),
        patch("app.services.chat.flog.warning") as warning,
    ):
        reply, tokens_in, tokens_out = _do_claude_stream(
            "https://api.example.test", {}, {}, 5
        )

    assert (reply, tokens_in, tokens_out) == ("ok", 0, 0)
    assert "Evento Anthropic inválido" in warning.call_args.args[0]


async def test_claude_done_event_includes_tokens():
    agent = _make_agent("claude", model="claude-3-5-sonnet-20241022")
    conn = _make_conn("claude", model="claude-3-5-sonnet-20241022")
    mock_resp = _sse_claude_response("Bonjour", input_tokens=20, output_tokens=8)

    with patch("app.connections.anthropic.safe_urlopen", return_value=mock_resp):
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"] == {"in": 20, "out": 8}


async def test_claude_emite_cada_delta_segun_llega():
    """Claude pedía "stream": true y se guardaba los deltas hasta el final.

    El usuario veía la pantalla quieta toda la generación y luego la respuesta
    de golpe, mientras que con cualquier proveedor OpenAI-compat la veía
    escribirse. Aquí se comprueba que sale un evento por delta y en orden.
    """
    agent = _make_agent("claude", model="claude-3-5-sonnet-20241022")
    conn = _make_conn("claude", model="claude-3-5-sonnet-20241022")

    trozos = ["Bon", "jour", " le", " monde"]
    lines = [
        b"data: "
        + json.dumps(
            {"type": "message_start", "message": {"usage": {"input_tokens": 20}}}
        ).encode()
        + b"\n"
    ]
    lines += [
        b"data: "
        + json.dumps({"type": "content_block_delta", "delta": {"text": t}}).encode()
        + b"\n"
        for t in trozos
    ]
    lines.append(
        b"data: "
        + json.dumps({"type": "message_delta", "usage": {"output_tokens": 8}}).encode()
        + b"\n"
    )
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = MagicMock(return_value=iter(lines))

    with patch("app.connections.anthropic.safe_urlopen", return_value=mock_resp):
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    emitidos = [
        json.loads(e.removeprefix("data: ").strip())["token"]
        for e in events
        if e.startswith("data: ") and '"type": "token"' in e
    ]
    assert emitidos == trozos

    done_event = next(e for e in events if '"type": "done"' in e)
    assert json.loads(done_event.removeprefix("data: ").strip())["reply"] == (
        "Bonjour le monde"
    )


def _ollama_response(
    reply: str = "Hi", prompt_eval_count: int = 15, eval_count: int = 6
) -> MagicMock:
    """Respuesta NDJSON de Ollama: un objeto JSON por línea, no SSE.

    Cada trozo del texto va en su propia línea; el recuento de tokens solo
    aparece en la última, la que lleva done:true.
    """
    trozos = [reply[i : i + 3] for i in range(0, len(reply), 3)] or [""]
    lines = [
        json.dumps({"message": {"content": t}, "done": False}).encode() + b"\n"
        for t in trozos
    ]
    lines.append(
        json.dumps(
            {
                "message": {"content": ""},
                "done": True,
                "prompt_eval_count": prompt_eval_count,
                "eval_count": eval_count,
            }
        ).encode()
        + b"\n"
    )
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = MagicMock(return_value=iter(lines))
    return mock_resp


async def test_ollama_done_event_includes_tokens():
    agent = _make_agent("ollama", model="llama3")
    conn = {**_make_conn("ollama", model="llama3"), "host": "http://localhost:11434"}
    mock_resp = _ollama_response("Ciao", prompt_eval_count=15, eval_count=6)

    with patch("app.connections.ollama.safe_urlopen", return_value=mock_resp):
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    done_event = next(e for e in events if '"type": "done"' in e)
    data = json.loads(done_event.removeprefix("data: ").strip())
    assert data["tokens"] == {"in": 15, "out": 6}


async def test_ollama_emite_cada_trozo_segun_llega():
    """Ollama era el último proveedor que devolvía la respuesta de una vez.

    Pedía "stream": False y el usuario se quedaba mirando una pantalla quieta,
    el mismo síntoma que tenía Claude antes de BE-10. Aquí se comprueba que
    salen eventos token, en orden, y que el texto reconstruido es el completo.
    """
    agent = _make_agent("ollama", model="llama3")
    conn = {**_make_conn("ollama", model="llama3"), "host": "http://localhost:11434"}
    mock_resp = _ollama_response("Hola mundo", prompt_eval_count=15, eval_count=6)

    with patch("app.connections.ollama.safe_urlopen", return_value=mock_resp):
        events = [
            e
            async for e in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    emitidos = [
        json.loads(e.removeprefix("data: ").strip())["token"]
        for e in events
        if e.startswith("data: ") and '"type": "token"' in e
    ]
    assert emitidos == ["Hol", "a m", "und", "o"]

    done_event = next(e for e in events if '"type": "done"' in e)
    assert (
        json.loads(done_event.removeprefix("data: ").strip())["reply"] == "Hola mundo"
    )


async def test_openai_usage_chunk_with_empty_choices():
    """El chunk de usage llega con choices:[] — no debe lanzar IndexError ni perder los tokens."""
    agent = _make_agent("nvidia", model="meta/llama-3.1-8b-instruct")
    conn = _make_conn("nvidia", model="meta/llama-3.1-8b-instruct")
    mock_resp = _sse_openai_with_usage("Hola", prompt_tokens=30, completion_tokens=12)

    with patch("app.connections.openai_compatible.safe_urlopen", return_value=mock_resp):
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

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
        [
            event
            async for event in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    assert sent_payloads[0]["max_tokens"] == 2_048
    assert sent_payloads[0]["chat_template_kwargs"] == expected_options
