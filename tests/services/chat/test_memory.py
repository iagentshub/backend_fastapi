"""Recuerdo de conversaciones anteriores y truncado del contexto."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services.chat import _estimate_tokens, _truncate_history, stream_chat
from tests.services.chat._helpers import (
    _make_agent,
    _make_conn,
    _skill_storage,
    _sse_done_response,
)

# ─── Tests de recuerdo de conversaciones anteriores ────────────────────────────


def _chat_storage_mock(convs: list, messages_by_conv: dict) -> MagicMock:
    storage = MagicMock()

    async def _list_memory_messages(
        user_id,
        agent_id,
        exclude_conversation_id=None,
        *,
        limit=200,
        chars_per_message=2000,
    ):
        result = []
        for conversation in convs:
            if conversation.get("id") == exclude_conversation_id:
                continue
            result.extend(messages_by_conv.get(conversation["id"], []))
        return result[:limit]

    storage.list_memory_messages = _list_memory_messages
    return storage


async def _sent_system_message(
    agent, conn, history, chat_storage, user_id, conversation_id
):
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("app.services.chat.providers.safe_urlopen", side_effect=fake_urlopen):
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
    chat_storage.list_memory_messages = MagicMock(
        side_effect=AssertionError("no debería llamarse sin user_id")
    )

    with patch("app.services.chat.providers.safe_urlopen", return_value=_sse_done_response()):
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


async def test_context_budget_truncates_resources_and_warns():
    agent = _make_agent("openai")
    agent["system_prompt"] = "x" * 260_000
    conn = _make_conn("openai")
    payloads = []

    def fake_urlopen(req, timeout):
        payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("app.services.chat.providers.safe_urlopen", side_effect=fake_urlopen):
        events = [
            event
            async for event in stream_chat(
                agent,
                conn,
                [{"role": "user", "content": "turno actual"}],
                _skill_storage(),
            )
        ]

    warning = next(event for event in events if '"type": "context_warning"' in event)
    assert '"code": "context_truncated"' in warning
    messages = payloads[0]["messages"]
    estimated = sum(_estimate_tokens(str(item["content"])) for item in messages)
    assert estimated <= 60_000 - 4_096


async def test_effort_level_is_sent_to_openai_compatible_provider():
    agent = _make_agent("openai")
    agent["effort_level"] = "high"
    conn = _make_conn("openai")
    sent_payloads = []

    def fake_urlopen(req, timeout):
        sent_payloads.append(json.loads(req.data.decode()))
        return _sse_done_response()

    with patch("app.services.chat.providers.safe_urlopen", side_effect=fake_urlopen):
        [
            event
            async for event in stream_chat(
                agent, conn, [{"role": "user", "content": "Hi"}], _skill_storage()
            )
        ]

    assert sent_payloads[0]["reasoning_effort"] == "high"


# ─── Tests de truncado de contexto ────────────────────────────────────────────


async def test_truncate_history_no_op():

    h = [{"role": "user", "content": "hola"}]
    assert _truncate_history(h, system_tokens=100) == h


async def test_truncate_history_descarta_antiguos():

    msgs = [{"role": "user", "content": "x" * 1000}] * 20  # ~5000 tokens
    result = _truncate_history(msgs, system_tokens=0, max_context=2000)
    assert len(result) < 20
    assert len(result) >= 2


async def test_estimate_tokens():

    assert _estimate_tokens("hola") == 1
    assert _estimate_tokens("a" * 400) == 100
