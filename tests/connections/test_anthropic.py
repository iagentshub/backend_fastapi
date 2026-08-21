"""Tests del provider Anthropic (Claude) — test() sin llamadas reales."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.connections.anthropic import AnthropicProvider


def _mock_response(data: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_test_missing_api_key():
    result = AnthropicProvider.test({"api_key": ""})
    assert result.ok is False


def test_test_success():
    mock_resp = _mock_response({"data": [{"id": "available-model"}]})
    with patch("app.connections.anthropic.safe_urlopen", return_value=mock_resp):
        result = AnthropicProvider.test({"api_key": "sk-ant-fake"})
    assert result.ok is True


def test_test_connection_error():
    with patch("app.connections.anthropic.safe_urlopen", side_effect=OSError("timeout")):
        result = AnthropicProvider.test({"api_key": "sk-ant-fake"})
    assert result.ok is False
