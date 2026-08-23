"""Connection providers package — import all to auto-register."""

# Import each provider to trigger @register
from . import (  # noqa: F401  # noqa: F401
    anthropic,
    database,
    github,
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
    ChatInvocation,
    FieldDef,
    TestResult,
    UnsafeProviderURL,
    account_providers,
    all_providers,
    get_account_provider,
    get_provider,
    is_chat_provider,
    register,
)

__all__ = [
    "BaseProvider",
    "ChatInvocation",
    "FieldDef",
    "TestResult",
    "UnsafeProviderURL",
    "account_providers",
    "all_providers",
    "get_account_provider",
    "is_chat_provider",
    "get_provider",
    "register",
]
