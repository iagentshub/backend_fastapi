"""Anthropic / Claude provider — fields + test."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Dict

from app.config.providers import (
    ANTHROPIC_API_VERSION,
    PROVIDER_BASE_URLS,
)
from app.utils.safe_http import safe_urlopen

from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["claude"]


@register
class AnthropicProvider(BaseProvider):
    type_id = "claude"
    label = "Anthropic (Claude)"
    icon = ""
    fields = [
        FieldDef("api_key", "API Key", "password", "sk-ant-...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/messages"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            req = urllib.request.Request(
                f"{_BASE_URL}/models?limit=1",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_API_VERSION,
                },
            )
            with safe_urlopen(req, timeout=15) as r:
                r.read()
            return TestResult(True, "OK — API key válida")
        except urllib.error.HTTPError as e:
            return TestResult(False, f"HTTP {e.code}", cls._http_error_msg(e))
        except (OSError, ValueError) as e:
            # OSError cubre URLError, timeouts y fallos de socket/DNS;
            # ValueError, el JSONDecodeError de una respuesta que no es JSON.
            # El mensaje viaja al usuario en TestResult.detail.
            return TestResult(False, "Error de conexión", str(e))
