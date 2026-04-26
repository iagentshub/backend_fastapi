"""Tests del provider Grok (xAI) — test() sin llamadas reales."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.connections.grok import GrokProvider


def _mock_response(data: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_test_missing_api_key():
    result = GrokProvider.test({"api_key": ""})
    assert result.ok is False


def test_test_success():
    mock_resp = _mock_response({"data": [{"id": "grok-3"}]})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = GrokProvider.test({"api_key": "xai-fake"})
    assert result.ok is True


def test_test_connection_error():
    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        result = GrokProvider.test({"api_key": "xai-fake"})
    assert result.ok is False
    assert "timeout" in result.detail
