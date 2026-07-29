"""Connection providers package — import all to auto-register."""

# Import each provider to trigger @register
from . import (  # noqa: F401  # noqa: F401
    anthropic,
    database,
    google,
    grok,
    iagentshub,
    nvidia,
    ollama,
    openai,
    qwen,
    ssh,
)
from .base import (
    BaseProvider,
    FieldDef,
    TestResult,
    all_providers,
    get_provider,
    register,
)

__all__ = [
    "BaseProvider",
    "FieldDef",
    "TestResult",
    "all_providers",
    "get_provider",
    "register",
]
