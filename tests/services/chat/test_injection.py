"""Inyección de knowledge adjuntado y de contenido de tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services.chat import stream_chat
from tests.services.chat._helpers import (
    _make_agent,
    _make_conn,
    _skill_storage,
    _sse_done_response,
)

# ─── Tests de knowledge adjuntado puntualmente ("attached_knowledge") ──────────


async def test_attached_knowledge_injected_into_system():
    """El contenido de un knowledge adjuntado puntualmente desde el chat debe
    aparecer en el system prompt, ya resuelto/autorizado por el llamador."""
    agent = _make_agent("openai")
    conn = _make_conn("openai")
    attached = [
        {"id": "kn-1", "title": "Guía de estilo", "content": "Usa siempre tono formal."}
    ]

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                attached_knowledge=attached,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "Usa siempre tono formal." in system_message


async def test_no_attached_knowledge_does_not_break_stream_chat():
    agent = _make_agent("openai")
    conn = _make_conn("openai")

    with patch("app.connections.openai_compatible.safe_urlopen", return_value=_sse_done_response()):
        events = [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
            )
        ]

    assert any("done" in e for e in events)


# ─── Tests de inyección de contenido de Tools (Fase 1.5) ───────────────────────


def _tool_storage_mock(tools_by_id: dict) -> MagicMock:
    storage = MagicMock()

    async def _get(scope, tool_id, owner_id=None):
        t = tools_by_id.get(tool_id)
        if t and t.get("scope", "public") == scope:
            return t
        return None

    storage.get = _get
    return storage


async def test_tool_python_script_injected_into_system():
    agent = _make_agent("openai")
    agent["tools"] = ["tool-1"]
    conn = _make_conn("openai")
    tool_storage = _tool_storage_mock(
        {
            "tool-1": {
                "id": "tool-1",
                "name": "Convertidor CSV",
                "language": "python",
                "content": "print('hola desde la tool')",
                "scope": "public",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                tool_storage=tool_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "print('hola desde la tool')" in system_message
    assert "no se ejecuta en el servidor" in system_message.lower()


async def test_tool_shell_script_from_private_scope_injected():
    agent = _make_agent("openai")
    agent["tools"] = ["tool-2"]
    conn = _make_conn("openai")
    tool_storage = _tool_storage_mock(
        {
            "tool-2": {
                "id": "tool-2",
                "name": "Backup de logs",
                "language": "shell",
                "content": "tar czf logs.tar.gz /var/log",
                "scope": "private",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                tool_storage=tool_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "tar czf logs.tar.gz /var/log" in system_message


async def test_tool_cpp_injects_metadata_not_binary():
    agent = _make_agent("openai")
    agent["tools"] = ["tool-3"]
    conn = _make_conn("openai")
    tool_storage = _tool_storage_mock(
        {
            "tool-3": {
                "id": "tool-3",
                "name": "Optimizador de imágenes",
                "language": "cpp",
                "content": "",
                "description": "Comprime PNG/JPEG sin pérdida perceptible.",
                "binary_b64": "AAAAFAKEBASE64==",
                "scope": "public",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                tool_storage=tool_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "Comprime PNG/JPEG sin pérdida perceptible." in system_message
    assert "Conocimiento" in system_message
    assert "AAAAFAKEBASE64==" not in system_message


async def test_tool_unknown_id_skipped_silently():
    agent = _make_agent("openai")
    agent["tools"] = ["tool-inexistente"]
    conn = _make_conn("openai")
    tool_storage = _tool_storage_mock({})

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
        [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                tool_storage=tool_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "## Tool:" not in system_message


async def test_no_tool_storage_is_backward_compatible():
    """Call-sites que no pasan tool_storage (default None) no deben romperse
    aunque el agente tenga tools asignadas."""
    agent = _make_agent("openai")
    agent["tools"] = ["tool-1"]
    conn = _make_conn("openai")

    with patch("app.connections.openai_compatible.safe_urlopen", return_value=_sse_done_response()):
        events = [
            e
            async for e in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
            )
        ]

    assert any("done" in e for e in events)
