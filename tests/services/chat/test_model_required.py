"""El chat nunca inventa un modelo cuando la conexión no tiene uno."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.services.chat import stream_chat
from tests.services.chat._helpers import _make_agent, _make_conn, _skill_storage


@pytest.mark.asyncio
async def test_missing_model_returns_actionable_error_without_calling_provider():
    agent = _make_agent("openai", model="")
    conn = _make_conn("openai", model="")
    events = []

    with patch("app.services.chat.providers.safe_urlopen") as urlopen:
        async for frame in stream_chat(
            agent,
            conn,
            [{"role": "user", "content": "hola"}],
            _skill_storage(),
        ):
            if frame.startswith("data: "):
                events.append(json.loads(frame[6:]))

    urlopen.assert_not_called()
    assert events == [
        {
            "type": "error",
            "code": "model_required",
            "message": "Selecciona un modelo del catálogo del proveedor.",
        }
    ]
