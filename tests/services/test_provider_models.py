"""Descubrimiento de catálogos de modelos sin IDs mantenidos en código."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.provider_models import fetch_provider_models


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "expected_url"),
    [
        ("grok", "https://api.x.ai/v1/models"),
        ("qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models"),
    ],
)
async def test_openai_compatible_catalogs_are_discovered(provider, expected_url):
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"data": [{"id": "model-b"}, {"id": "model-a"}]}
    ).encode()
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch(
        "app.connections.openai_compatible.safe_urlopen", return_value=response
    ) as urlopen:
        models = await fetch_provider_models(provider, "secret")

    assert models == ["model-a", "model-b"]
    request = urlopen.call_args.args[0]
    assert request.full_url == expected_url
    assert request.get_header("Authorization") == "Bearer secret"


@pytest.mark.asyncio
async def test_google_catalog_only_returns_generate_content_models():
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "models": [
                {
                    "name": "models/chat-model",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/embedding-model",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }
    ).encode()
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch("app.connections.google.safe_urlopen", return_value=response):
        models = await fetch_provider_models("google", "secret")

    assert models == ["chat-model"]
