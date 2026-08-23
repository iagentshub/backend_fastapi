"""GitHub Models — catálogo y chat OpenAI-compatible."""

from __future__ import annotations

from .base import FieldDef, register
from .openai_compatible import OpenAICompatibleProvider

_BASE_URL = "https://models.inference.ai.azure.com"


@register
class GitHubModelsProvider(OpenAICompatibleProvider):
    type_id = "github"
    account_type_id = "github"
    label = "GitHub Copilot"
    icon = ""
    base_url = _BASE_URL
    default_chat_url = f"{_BASE_URL}/chat/completions"
    fields = [
        FieldDef("api_key", "GitHub Token", "password", "ghp_...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=default_chat_url),
    ]
