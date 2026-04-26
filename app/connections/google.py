"""Google Gemini provider — fields + test."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from app.config.providers import PROVIDER_BASE_URLS, PROVIDER_DEFAULT_MODELS
from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["gemini"]


@register
class GoogleProvider(BaseProvider):
    type_id = "gemini"
    label = "Google Gemini"
    icon = "🔵"
    fields = [
        FieldDef("api_key", "API Key", "password", "AIza...", required=True),
        FieldDef("model", "Modelo por defecto", "text", PROVIDER_DEFAULT_MODELS["gemini"]),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            url = f"{_BASE_URL}/models?key={api_key}&pageSize=5"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            count = len(data.get("models") or [])
            return TestResult(True, f"OK — {count} modelos disponibles")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                msg = json.loads(body).get("error", {}).get("message", body)
            except Exception:
                msg = body[:200]
            return TestResult(False, f"HTTP {e.code}", msg)
        except Exception as e:
            return TestResult(False, "Error de conexión", str(e))
