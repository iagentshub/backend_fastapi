"""Tests del provider NVIDIA NIM — test() sin llamadas reales (mock de urllib)."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from app.connections.nvidia import NvidiaProvider


def _mock_response(data: dict, status: int = 200):
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.status = status
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_test_missing_api_key():
    result = NvidiaProvider.test({"api_key": ""})
    assert result.ok is False
    assert "API Key" in result.message


def test_test_success():
    mock_resp = _mock_response({"data": [{"id": "available-model"}]})
    with patch("app.connections.openai_compatible.safe_urlopen", return_value=mock_resp):
        result = NvidiaProvider.test({"api_key": "nvapi-fake"})
    assert result.ok is True


def test_test_auth_error():
    http_err = urllib.error.HTTPError(
        url="", code=401, msg="Unauthorized",
        hdrs=None, fp=None,  # type: ignore
    )
    http_err.read = lambda: b'{"error": {"message": "Invalid API key"}}'
    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=http_err):
        result = NvidiaProvider.test({"api_key": "nvapi-bad"})
    assert result.ok is False
    assert result.message == "Error de autenticación"
    assert result.detail == "Invalid API key"


def test_test_connection_error():
    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=OSError("timeout")):
        result = NvidiaProvider.test({"api_key": "nvapi-fake"})
    assert result.ok is False
    assert "timeout" in result.detail
