"""Anthropic / Claude provider — fields + test."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from app.config.providers import (
    ANTHROPIC_API_VERSION,
    ANTHROPIC_TEST_MODEL,
    PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS,
)
from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["claude"]


@register
class AnthropicProvider(BaseProvider):
    type_id = "claude"
    label = "Anthropic (Claude)"
    icon = "🟠"
    fields = [
        FieldDef("api_key", "API Key", "password", "sk-ant-...", required=True),
        FieldDef("model", "Modelo por defecto", "text", PROVIDER_DEFAULT_MODELS["claude"]),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/messages"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            payload = json.dumps({
                "model": (config.get("model") or ANTHROPIC_TEST_MODEL).strip() or ANTHROPIC_TEST_MODEL,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }).encode()
            req = urllib.request.Request(
                f"{_BASE_URL}/messages",
                data=payload,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_API_VERSION,
                    "content-type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                json.loads(r.read())
            return TestResult(True, "OK — API key válida")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                msg = json.loads(body).get("error", {}).get("message", body)
            except Exception:
                msg = body[:200]
            return TestResult(False, f"HTTP {e.code}", msg)
        except Exception as e:
            return TestResult(False, "Error de conexión", str(e))
