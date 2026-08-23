"""OpenAI provider — fields + test."""
from __future__ import annotations

from app.config.providers import PROVIDER_BASE_URLS

from .base import FieldDef, register
from .openai_compatible import OpenAICompatibleProvider

_BASE_URL = PROVIDER_BASE_URLS["openai"]


@register
class OpenAIProvider(OpenAICompatibleProvider):
    type_id = "openai"
    account_type_id = "openai"
    label = "OpenAI"
    icon = ""
    base_url = _BASE_URL
    default_chat_url = f"{_BASE_URL}/chat/completions"
    fields = [
        FieldDef("api_key", "API Key", "password", "sk-...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/chat/completions"),
    ]
