"""Tests del provider OpenAI — test() sin llamadas reales (mock de urllib)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.connections.openai import OpenAIProvider


def _mock_response(data: dict, status: int = 200):
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.status = status
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_test_missing_api_key():
    result = OpenAIProvider.test({"api_key": ""})
    assert result.ok is False
    assert "API Key" in result.message


def test_test_success():
    mock_resp = _mock_response({"data": [{"id": "gpt-4o"}, {"id": "gpt-3.5"}]})
    with patch("app.connections.base.safe_urlopen", return_value=mock_resp):
        result = OpenAIProvider.test({"api_key": "sk-fake"})
    assert result.ok is True
    assert "2" in result.message


def test_test_auth_error():
    import urllib.error
    http_err = urllib.error.HTTPError(
        url="", code=401, msg="Unauthorized",
        hdrs=None, fp=None,  # type: ignore
    )
    http_err.read = lambda: b'{"error": {"message": "Invalid API key"}}'
    with patch("app.connections.base.safe_urlopen", side_effect=http_err):
        result = OpenAIProvider.test({"api_key": "sk-bad"})
    assert result.ok is False


def test_test_connection_error():
    with patch("app.connections.base.safe_urlopen", side_effect=OSError("timeout")):
        result = OpenAIProvider.test({"api_key": "sk-fake"})
    assert result.ok is False
    assert "timeout" in result.detail
