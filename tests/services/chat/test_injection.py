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

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
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

    with patch(
        "app.connections.openai_compatible.safe_urlopen",
        return_value=_sse_done_response(),
    ):
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


async def test_inactive_attached_knowledge_is_not_injected():
    agent = _make_agent("openai")
    conn = _make_conn("openai")
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
        [
            event
            async for event in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                attached_knowledge=[
                    {
                        "id": "inactive",
                        "title": "No usar",
                        "content": "CONTENIDO_DESACTIVADO",
                        "is_active": False,
                    }
                ],
            )
        ]

    assert "CONTENIDO_DESACTIVADO" not in sent_payloads[0]["messages"][0]["content"]


async def test_inactive_linked_knowledge_pack_is_not_injected():
    agent = _make_agent("openai")
    agent["knowledge_packs"] = ["pack-off"]
    conn = _make_conn("openai")
    pack_storage = MagicMock()
    knowledge_storage = MagicMock()

    async def get_pack(pack_id):
        return {
            "id": pack_id,
            "name": "Pack apagado",
            "is_active": False,
            "items": [{"id": "doc-off", "relative_path": "manual.md"}],
        }

    async def get_knowledge(item_id):
        return {"id": item_id, "content": "CONTENIDO_DESACTIVADO"}

    pack_storage.get = get_pack
    knowledge_storage.get = get_knowledge
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
        [
            event
            async for event in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                knowledge_storage=knowledge_storage,
                knowledge_pack_storage=pack_storage,
            )
        ]

    assert "CONTENIDO_DESACTIVADO" not in sent_payloads[0]["messages"][0]["content"]


# ─── Tests de inyección de contenido de Tools (Fase 1.5) ───────────────────────


async def test_inactive_linked_skill_is_not_injected():
    agent = _make_agent("openai")
    agent["skills"] = ["skill-off"]
    conn = _make_conn("openai")
    skill_storage = MagicMock()

    async def get_skill(scope, skill_id):
        if scope != "public":
            return None
        return {
            "id": skill_id,
            "name": "Skill apagada",
            "content": "CONTENIDO_DESACTIVADO",
            "is_active": False,
        }

    skill_storage.get = get_skill
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
        [
            event
            async for event in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                skill_storage,
            )
        ]

    assert "CONTENIDO_DESACTIVADO" not in sent_payloads[0]["messages"][0]["content"]


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
                "instructions": "Convierte un CSV local conservando las columnas.",
                "input_schema": {"type": "object", "required": ["path"]},
                "output_schema": {"type": "object", "required": ["rows"]},
                "scope": "public",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
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
    assert "Convierte un CSV local conservando las columnas." in system_message
    assert "print('hola desde la tool')" not in system_message
    assert "servidor nunca la ejecuta" in system_message.lower()
    assert 'Entrada esperada: {"type":"object","required":["path"]}' in system_message
    assert 'Salida esperada: {"type":"object","required":["rows"]}' in system_message


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
                "instructions": "Crea una copia comprimida de los logs elegidos.",
                "scope": "private",
            }
        }
    )

    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
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
    assert "Crea una copia comprimida de los logs elegidos." in system_message
    assert "tar czf logs.tar.gz /var/log" not in system_message


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

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
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
    assert "implementación nativa" in system_message
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

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
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


async def test_inactive_linked_tool_is_not_injected():
    agent = _make_agent("openai")
    agent["tools"] = ["tool-off"]
    conn = _make_conn("openai")
    storage = _tool_storage_mock(
        {
            "tool-off": {
                "id": "tool-off",
                "name": "Tool apagada",
                "language": "python",
                "content": "CONTENIDO_DESACTIVADO",
                "scope": "public",
                "is_active": False,
            }
        }
    )
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
        [
            event
            async for event in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "Hola"}],
                _skill_storage(),
                tool_storage=storage,
            )
        ]

    assert "CONTENIDO_DESACTIVADO" not in sent_payloads[0]["messages"][0]["content"]


async def test_no_tool_storage_is_backward_compatible():
    """Call-sites que no pasan tool_storage (default None) no deben romperse
    aunque el agente tenga tools asignadas."""
    agent = _make_agent("openai")
    agent["tools"] = ["tool-1"]
    conn = _make_conn("openai")

    with patch(
        "app.connections.openai_compatible.safe_urlopen",
        return_value=_sse_done_response(),
    ):
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
