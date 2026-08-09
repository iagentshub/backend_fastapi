"""Tests del provider Google Gemini — test() sin llamadas reales."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.connections.google import GoogleProvider


def _mock_response(data: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_test_missing_api_key():
    result = GoogleProvider.test({"api_key": ""})
    assert result.ok is False


def test_test_success():
    mock_resp = _mock_response({"models": [{"name": "gemini-2.0-flash"}, {"name": "gemini-1.5"}]})
    with patch("app.connections.google.safe_urlopen", return_value=mock_resp):
        result = GoogleProvider.test({"api_key": "AIza-fake"})
    assert result.ok is True
    assert "2" in result.message


def test_test_connection_error():
    with patch("app.connections.google.safe_urlopen", side_effect=Exception("timeout")):
        result = GoogleProvider.test({"api_key": "AIza-fake"})
    assert result.ok is False
