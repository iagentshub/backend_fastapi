"""Qwen (Alibaba DashScope) provider — fields + test."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from app.config.providers import PROVIDER_BASE_URLS, PROVIDER_DEFAULT_MODELS
from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["qwen"]


@register
class QwenProvider(BaseProvider):
    type_id = "qwen"
    label = "Qwen (Alibaba)"
    icon = ""
    fields = [
        FieldDef("api_key", "API Key", "password", "sk-...", required=True),
        FieldDef("model", "Modelo por defecto", "text", PROVIDER_DEFAULT_MODELS["qwen"]),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/chat/completions"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            req = urllib.request.Request(
                f"{_BASE_URL}/models",
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
