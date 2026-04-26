"""Ollama provider — fields + test."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from .base import BaseProvider, FieldDef, TestResult, register


@register
class OllamaProvider(BaseProvider):
    type_id = "ollama"
    label = "Ollama"
    icon = "⚫"
    fields = [
        FieldDef("host", "URL del servidor", "text", "http://localhost:11434", required=True),
        FieldDef("model", "Modelo por defecto", "text", "llama3"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        host = (config.get("host") or "").strip().rstrip("/")
        if not host:
            return TestResult(False, "Falta la URL del servidor")
        try:
            req = urllib.request.Request(f"{host}/api/tags")
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            models = data.get("models") or []
            names = ", ".join(m.get("name", "?") for m in models[:5])
            suffix = f"… (+{len(models)-5})" if len(models) > 5 else ""
            return TestResult(True, f"OK — {len(models)} modelos", f"{names}{suffix}")
        except urllib.error.HTTPError as e:
            return TestResult(False, f"HTTP {e.code}", str(e))
        except Exception as e:
            return TestResult(False, "Sin conexión al servidor Ollama", str(e))
