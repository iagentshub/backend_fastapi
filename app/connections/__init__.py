"""Connection providers package — import all to auto-register."""
from .base import BaseProvider, FieldDef, TestResult, all_providers, get_provider, register

# Import each provider to trigger @register
from . import anthropic, google, grok, nvidia, ollama, openai, qwen  # noqa: F401

__all__ = [
    "BaseProvider",
    "FieldDef",
    "TestResult",
    "all_providers",
    "get_provider",
    "register",
]
