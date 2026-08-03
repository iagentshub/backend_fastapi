"""Configuración de proveedores LLM — fuente única de verdad."""
from __future__ import annotations

import os

# OAuth App propia (Device Flow habilitado) para "Conectar con GitHub" desde
# Accounts, en vez de pegar un Personal Access Token a mano. Sin client_id
# configurado, ese botón queda deshabilitado (fallback: pegar el token).
GITHUB_OAUTH_CLIENT_ID: str = os.getenv("GITHUB_OAUTH_CLIENT_ID", "")

PROVIDER_BASE_URLS: dict[str, str] = {
    "openai":  "https://api.openai.com/v1",
    "gemini":  "https://generativelanguage.googleapis.com/v1beta",
    "qwen":    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "grok":    "https://api.x.ai/v1",
    "claude":  "https://api.anthropic.com/v1",
    "nvidia":  "https://integrate.api.nvidia.com/v1",
}

# Gemini usa /openai/chat/completions como prefijo de compatibilidad
OPENAI_COMPAT_URLS: dict[str, str] = {
    "openai":  f"{PROVIDER_BASE_URLS['openai']}/chat/completions",
    "gemini":  f"{PROVIDER_BASE_URLS['gemini']}/openai/chat/completions",
    "qwen":    f"{PROVIDER_BASE_URLS['qwen']}/chat/completions",
    "grok":    f"{PROVIDER_BASE_URLS['grok']}/chat/completions",
    "nvidia":  f"{PROVIDER_BASE_URLS['nvidia']}/chat/completions",
}

PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai":  "gpt-4o",
    "gemini":  "gemini-2.0-flash",
    "qwen":    "qwen-plus",
    "grok":    "grok-3",
    "claude":  "claude-sonnet-4-6",
    "nvidia":  "meta/llama-3.1-8b-instruct",
}

# Modelo ligero para el test de conectividad de Anthropic (minimiza coste del ping)
ANTHROPIC_TEST_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_API_VERSION = "2023-06-01"
