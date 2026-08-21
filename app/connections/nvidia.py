"""NVIDIA NIM provider — fields + test."""

from __future__ import annotations

from typing import Any, Dict

from app.config.providers import PROVIDER_BASE_URLS

from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["nvidia"]


@register
class NvidiaProvider(BaseProvider):
    type_id = "nvidia"
    label = "NVIDIA NIM"
    icon = ""
    fields = [
        FieldDef("api_key", "API Key", "password", "nvapi-...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/chat/completions"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        """Valida la credencial contra el catálogo, sin generar ni fijar un modelo."""
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        return cls._test_openai_models(api_key, _BASE_URL)
