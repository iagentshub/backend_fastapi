"""Tests del provider Qwen (Alibaba) — test() sin llamadas reales."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.connections.qwen import QwenProvider


def _mock_response(data: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_test_missing_api_key():
    result = QwenProvider.test({"api_key": ""})
    assert result.ok is False


def test_test_success():
    mock_resp = _mock_response({"data": [{"id": "qwen-plus"}]})
    with patch("app.connections.openai_compatible.safe_urlopen", return_value=mock_resp):
        result = QwenProvider.test({"api_key": "fake-dashscope-key"})
    assert result.ok is True


def test_test_connection_error():
    with patch("app.connections.openai_compatible.safe_urlopen", side_effect=OSError("timeout")):
        result = QwenProvider.test({"api_key": "fake-key"})
    assert result.ok is False
