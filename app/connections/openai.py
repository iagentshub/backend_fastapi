"""OpenAI provider — fields + test."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from .base import BaseProvider, FieldDef, TestResult, register


@register
class OpenAIProvider(BaseProvider):
    type_id = "openai"
    label = "OpenAI"
    icon = "🟢"
    fields = [
        FieldDef("api_key", "API Key", "password", "sk-...", required=True),
        FieldDef("model", "Modelo por defecto", "text", "gpt-4o"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            req = urllib.request.Request(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            count = len(data.get("data") or [])
            return TestResult(True, f"OK — {count} modelos disponibles")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                msg = json.loads(body).get("error", {}).get("message", body)
            except Exception:
                msg = body[:200]
            return TestResult(False, "Error de autenticación", msg)
        except Exception as e:
            return TestResult(False, "Error de conexión", str(e))
