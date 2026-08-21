"""Grok (xAI) provider — fields + test."""
from __future__ import annotations

from typing import Any, Dict

from app.config.providers import PROVIDER_BASE_URLS

from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["grok"]


@register
class GrokProvider(BaseProvider):
    type_id = "grok"
    label = "Grok (xAI)"
    icon = ""
    fields = [
        FieldDef("api_key", "API Key", "password", "xai-...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/chat/completions"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        return cls._test_openai_models((config.get("api_key") or "").strip(), _BASE_URL)
