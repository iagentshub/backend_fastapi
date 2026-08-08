"""Dobles compartidos por los tests de `app/services/chat.py`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock


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
