"""Tests del provider Ollama — test() sin llamadas reales."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.connections.ollama import OllamaProvider


def _mock_response(data: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_test_missing_host():
    result = OllamaProvider.test({"host": ""})
    assert result.ok is False


def test_test_success_with_models():
    models = [{"name": f"model{i}"} for i in range(3)]
    mock_resp = _mock_response({"models": models})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = OllamaProvider.test({"host": "http://localhost:11434"})
    assert result.ok is True
    assert "3" in result.message


def test_test_success_no_models():
    mock_resp = _mock_response({"models": []})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = OllamaProvider.test({"host": "http://localhost:11434"})
    assert result.ok is True


def test_test_connection_error():
    with patch("urllib.request.urlopen", side_effect=Exception("refused")):
        result = OllamaProvider.test({"host": "http://localhost:11434"})
    assert result.ok is False
