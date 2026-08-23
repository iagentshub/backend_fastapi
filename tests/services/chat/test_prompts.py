"""Inyección de prompts por mención "@alias"."""

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

# ─── Tests de inyección de prompts vía mención "@alias" ────────────────────────


def _prompt_storage_mock(prompts_by_id: dict) -> MagicMock:
    storage = MagicMock()

    async def _get_any(prompt_id, owner_id=None):
        return prompts_by_id.get(prompt_id)

    async def _find_by_alias(alias, owner_id=None):
        alias = alias.strip().lower()
        candidates = [
            p for p in prompts_by_id.values()
            if str(p.get("alias", "")).lower() == alias
        ]
        for p in candidates:
            if owner_id and p.get("owner_id") == owner_id:
                return p
        for p in candidates:
            if p.get("scope", "public") == "public":
                return p
        return None

    storage.get_any = _get_any
    storage.find_by_alias = _find_by_alias
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

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
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

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
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

    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen):
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


async def test_mention_of_prompt_not_in_agent_catalog_is_resolved():
    """El alias mencionado se resuelve contra cualquier prompt accesible del
    usuario (público o propio), no solo contra los vinculados al agente."""
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
                [{"role": "user", "content": "Hola @resumen"}],
                _skill_storage(),
                prompt_storage=prompt_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "Resume el texto en 3 frases." in system_message


async def test_mention_of_own_private_prompt_not_in_agent_catalog_is_resolved():
    """Un prompt privado propio del usuario, aunque no esté vinculado al
    agente, debe poder mencionarse."""
    agent = _make_agent("openai")
    agent["prompts"] = []
    conn = _make_conn("openai")
    prompt_storage = _prompt_storage_mock(
        {
            "prompt-1": {
                "id": "prompt-1",
                "alias": "resumen",
                "name": "Resumen",
                "content": "Resume el texto en 3 frases.",
                "scope": "private",
                "owner_id": "alice",
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
                [{"role": "user", "content": "Hola @resumen"}],
                _skill_storage(),
                user_id="alice",
                prompt_storage=prompt_storage,
            )
        ]

    system_message = sent_payloads[0]["messages"][0]["content"]
    assert "Resume el texto en 3 frases." in system_message


async def test_mention_of_other_owners_private_prompt_is_ignored():
    """Un prompt privado de otro usuario nunca debe filtrarse por mención,
    aunque el alias coincida exactamente."""
    agent = _make_agent("openai")
    agent["prompts"] = []
    conn = _make_conn("openai")
    prompt_storage = _prompt_storage_mock(
        {
            "prompt-1": {
                "id": "prompt-1",
                "alias": "resumen",
                "name": "Resumen",
                "content": "Resume el texto en 3 frases.",
                "scope": "private",
                "owner_id": "bob",
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
                [{"role": "user", "content": "Hola @resumen"}],
                _skill_storage(),
                user_id="alice",
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

    with patch("app.connections.openai_compatible.safe_urlopen", return_value=_sse_done_response()):
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
