"""Base types and registry for connection providers."""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional


@dataclass
class FieldDef:
    key: str
    label: str
    type: str = "text"  # text | password | number | select | textarea | checkbox
    placeholder: str = ""
    required: bool = False
    default: str = ""
    options: List[Dict[str, str]] = field(default_factory=list)  # for select
    depends_on: Optional[str] = None  # show only when another field == depends_value
    depends_value: Optional[str] = None


@dataclass
class TestResult:
    __test__: ClassVar[bool] = False

    ok: bool
    message: str
    detail: str = ""


@dataclass(frozen=True)
class ChatInvocation:
    """Trabajo bloqueante ya preparado por un proveedor para ``_stream_tokens``."""

    worker: Callable[..., tuple[str, int, int]]
    args: tuple[Any, ...]
    url: str = ""


class UnsafeProviderURL(ValueError):
    """La configuración intenta alcanzar un destino que su política no permite."""


class BaseProvider:
    type_id: str = ""
    label: str = ""
    icon: str = "🔌"
    category: str = "llm"  # llm | machine | database
    account_type_id: str = ""
    supports_chat: bool = False
    expand_models_on_list: bool = False
    visible: bool = True
    fields: ClassVar[List[FieldDef]] = []

    @classmethod
    def _http_error_msg(cls, e: urllib.error.HTTPError) -> str:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body).get("error", {}).get("message", body)
        except (json.JSONDecodeError, AttributeError):
            # El cuerpo del error no es JSON, o es JSON pero no un objeto con la
            # forma {"error": {"message": …}} — cada proveedor responde a su
            # manera. Se devuelve el cuerpo crudo recortado.
            return body[:200]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:  # noqa: D102
        raise NotImplementedError

    @classmethod
    def validate_config(
        cls, config: Dict[str, Any], *, purpose: str = "use"
    ) -> None:
        """Valida sintaxis y política; las subclases controlan sus excepciones."""

    @classmethod
    def fetch_models(cls, config: Dict[str, Any]) -> List[str]:
        raise NotImplementedError(f"{cls.type_id} no expone catálogo de modelos")

    @classmethod
    def prepare_chat(
        cls,
        config: Dict[str, Any],
        *,
        model: str,
        history: List[Dict[str, Any]],
        system: str,
        temperature: float,
        max_tokens: int | None,
        effort_level: str | None,
        timeout: int | None,
    ) -> ChatInvocation:
        raise NotImplementedError(f"{cls.type_id} no soporta chat")

    @classmethod
    def http_error_detail(cls, status: int, model: str, invocation: ChatInvocation) -> str | None:
        return None


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, type] = {}
_ACCOUNT_REGISTRY: Dict[str, type] = {}


def register(cls: type) -> type:
    _REGISTRY[cls.type_id] = cls
    if cls.account_type_id:
        _ACCOUNT_REGISTRY[cls.account_type_id] = cls
    return cls


def get_provider(type_id: str) -> Optional[type]:
    return _REGISTRY.get(type_id)


def get_account_provider(type_id: str) -> Optional[type]:
    return _ACCOUNT_REGISTRY.get(type_id)


def account_providers() -> Dict[str, type]:
    return dict(_ACCOUNT_REGISTRY)


def is_chat_provider(type_id: str) -> bool:
    provider = get_provider(type_id)
    return bool(provider and provider.supports_chat)


def all_providers() -> List[Dict[str, Any]]:
    """Return provider metadata for the frontend form builder."""
    return [
        {
            "type": p.type_id,
            "label": p.label,
            "icon": p.icon,
            "category": p.category,
            "fields": [f.__dict__ for f in p.fields],
        }
        for p in _REGISTRY.values()
        if p.visible
    ]
