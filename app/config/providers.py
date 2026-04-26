"""Configuración de proveedores LLM — fuente única de verdad."""
from __future__ import annotations

PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "qwen":   "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "grok":   "https://api.x.ai/v1",
    "claude": "https://api.anthropic.com/v1",
}

# Gemini usa /openai/chat/completions como prefijo de compatibilidad
OPENAI_COMPAT_URLS: dict[str, str] = {
    "openai": f"{PROVIDER_BASE_URLS['openai']}/chat/completions",
    "gemini": f"{PROVIDER_BASE_URLS['gemini']}/openai/chat/completions",
    "qwen":   f"{PROVIDER_BASE_URLS['qwen']}/chat/completions",
    "grok":   f"{PROVIDER_BASE_URLS['grok']}/chat/completions",
}

PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "qwen":   "qwen-plus",
    "grok":   "grok-3",
    "claude": "claude-sonnet-4-5",
}

# Modelo ligero para el test de conectividad de Anthropic (minimiza coste del ping)
ANTHROPIC_TEST_MODEL = "claude-haiku-3-5"
ANTHROPIC_API_VERSION = "2023-06-01"
