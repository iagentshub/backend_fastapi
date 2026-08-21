"""Descubrimiento de catálogos de modelos sin IDs mantenidos en código."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
    response.json.return_value = {"data": [{"id": "model-b"}, {"id": "model-a"}]}
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client

    with patch("app.services.provider_models.httpx.AsyncClient", return_value=context):
        models = await fetch_provider_models(provider, "secret")

    assert models == ["model-a", "model-b"]
    client.get.assert_awaited_once_with(
        expected_url,
        headers={"Authorization": "Bearer secret"},
    )


@pytest.mark.asyncio
async def test_google_catalog_only_returns_generate_content_models():
    response = MagicMock()
    response.json.return_value = {
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
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client

    with patch("app.services.provider_models.httpx.AsyncClient", return_value=context):
        models = await fetch_provider_models("google", "secret")

    assert models == ["chat-model"]
