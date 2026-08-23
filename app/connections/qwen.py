"""Qwen (Alibaba DashScope) provider — fields + test."""
from __future__ import annotations

from app.config.providers import PROVIDER_BASE_URLS

from .base import FieldDef, register
from .openai_compatible import OpenAICompatibleProvider

_BASE_URL = PROVIDER_BASE_URLS["qwen"]


@register
class QwenProvider(OpenAICompatibleProvider):
    type_id = "qwen"
    label = "Qwen (Alibaba)"
    icon = ""
    base_url = _BASE_URL
    default_chat_url = f"{_BASE_URL}/chat/completions"
    fields = [
        FieldDef("api_key", "API Key", "password", "sk-...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/chat/completions"),
    ]
