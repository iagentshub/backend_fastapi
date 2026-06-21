"""NVIDIA NIM provider — fields + test."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from app.config.providers import PROVIDER_BASE_URLS, PROVIDER_DEFAULT_MODELS
from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["nvidia"]
_TEST_MODEL = "meta/llama-3.1-8b-instruct"


@register
class NvidiaProvider(BaseProvider):
    type_id = "nvidia"
    label = "NVIDIA NIM"
    icon = ""
    fields = [
        FieldDef("api_key", "API Key", "password", "nvapi-...", required=True),
        FieldDef("model", "Modelo por defecto", "text", PROVIDER_DEFAULT_MODELS["nvidia"]),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/chat/completions"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        model = (config.get("model") or "").strip() or _TEST_MODEL
        try:
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }).encode()
            req = urllib.request.Request(
                f"{_BASE_URL}/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
            return TestResult(True, "OK")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                msg = json.loads(body).get("detail") or json.loads(body).get("message") or body[:200]
            except Exception:
                msg = body[:200]
            return TestResult(False, f"HTTP {e.code}", msg)
        except Exception as e:
            return TestResult(False, "Error de conexión", str(e))
