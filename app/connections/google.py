"""Google Gemini provider — fields + test."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from app.config.providers import PROVIDER_BASE_URLS
from app.utils.safe_http import safe_urlopen

from .base import FieldDef, TestResult, register
from .openai_compatible import OpenAICompatibleProvider

_BASE_URL = PROVIDER_BASE_URLS["gemini"]


@register
class GoogleProvider(OpenAICompatibleProvider):
    type_id = "gemini"
    account_type_id = "google"
    label = "Google Gemini"
    icon = ""
    base_url = _BASE_URL
    default_chat_url = f"{_BASE_URL}/openai/chat/completions"
    fields = [
        FieldDef("api_key", "API Key", "password", "AIza...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/openai/chat/completions"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            url = f"{_BASE_URL}/models?key={api_key}&pageSize=5"
            req = urllib.request.Request(url)
            with safe_urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            count = len(data.get("models") or [])
            return TestResult(True, f"OK — {count} modelos disponibles")
        except urllib.error.HTTPError as e:
            return TestResult(False, f"HTTP {e.code}", cls._http_error_msg(e))
        except (OSError, ValueError) as e:
            # OSError cubre URLError, timeouts y fallos de socket/DNS;
            # ValueError, el JSONDecodeError de una respuesta que no es JSON.
            # El mensaje viaja al usuario en TestResult.detail.
            return TestResult(False, "Error de conexión", str(e))

    @classmethod
    def fetch_models(cls, config: Dict[str, Any]) -> list[str]:
        api_key = str(config.get("api_key") or "").strip()
        request = urllib.request.Request(
            f"{_BASE_URL}/models?key={api_key}&pageSize=100"
        )
        with safe_urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
        return [
            item["name"].split("/")[-1]
            for item in (data.get("models") or [])
            if "generateContent" in item.get("supportedGenerationMethods", [])
        ]
