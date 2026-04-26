"""Base types and registry for connection providers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FieldDef:
    key: str
    label: str
    type: str = "text"           # text | password | number | select
    placeholder: str = ""
    required: bool = False
    options: List[Dict[str, str]] = field(default_factory=list)   # for select
    depends_on: Optional[str] = None   # show only when another field == depends_value
    depends_value: Optional[str] = None


@dataclass
class TestResult:
    ok: bool
    message: str
    detail: str = ""


class BaseProvider:
    type_id: str = ""
    label: str = ""
    icon: str = "🔌"
    fields: List[FieldDef] = []

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:  # noqa: D102
        raise NotImplementedError


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, type] = {}


def register(cls: type) -> type:
    _REGISTRY[cls.type_id] = cls
    return cls


def get_provider(type_id: str) -> Optional[type]:
    return _REGISTRY.get(type_id)


def all_providers() -> List[Dict[str, Any]]:
    """Return provider metadata for the frontend form builder."""
    return [
        {
            "type": p.type_id,
            "label": p.label,
            "icon": p.icon,
            "fields": [f.__dict__ for f in p.fields],
        }
        for p in _REGISTRY.values()
    ]
