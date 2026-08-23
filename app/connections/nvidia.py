"""NVIDIA NIM provider — fields + test."""

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import urlparse, urlunparse

from app.config.providers import PROVIDER_BASE_URLS

from .base import FieldDef, register
from .openai_compatible import OpenAICompatibleProvider

_BASE_URL = PROVIDER_BASE_URLS["nvidia"]


@register
class NvidiaProvider(OpenAICompatibleProvider):
    type_id = "nvidia"
    label = "NVIDIA NIM"
    icon = ""
    account_type_id = "nvidia"
    base_url = _BASE_URL
    default_chat_url = f"{_BASE_URL}/chat/completions"
    fields = [
        FieldDef("api_key", "API Key", "password", "nvapi-...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/chat/completions"),
    ]

    _DEEPSEEK_V4_MODELS = {
        "deepseek-ai/deepseek-v4-pro",
        "deepseek-ai/deepseek-v4-flash",
    }

    @classmethod
    def _chat_url(cls, configured_url: str = "") -> str:
        parsed = urlparse(configured_url.strip())
        if parsed.netloc.casefold() == "integrate.api.nvidia.com":
            return urlunparse(
                parsed._replace(
                    path=urlparse(cls.default_chat_url).path,
                    params="",
                    query="",
                    fragment="",
                )
            )
        return super()._chat_url(configured_url)

    @classmethod
    def _augment_payload(
        cls, payload: Dict[str, Any], *, model: str, max_tokens: int | None
    ) -> None:
        if model not in cls._DEEPSEEK_V4_MODELS:
            return
        if not max_tokens:
            payload["max_tokens"] = 2_048
        payload["chat_template_kwargs"] = {"thinking": False}
        if model == "deepseek-ai/deepseek-v4-flash":
            payload["chat_template_kwargs"]["reasoning_effort"] = "none"
