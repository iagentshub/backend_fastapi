"""Base types and registry for connection providers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

from app.utils.safe_http import safe_urlopen


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


class BaseProvider:
    type_id: str = ""
    label: str = ""
    icon: str = "🔌"
    category: str = "llm"  # llm | machine | database
    fields: ClassVar[List[FieldDef]] = []

    @classmethod
    def _http_error_msg(cls, e: urllib.error.HTTPError) -> str:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body).get("error", {}).get("message", body)
        except Exception:
            return body[:200]

    @classmethod
    def _test_openai_models(cls, api_key: str, base_url: str) -> TestResult:
        """Test para proveedores con endpoint /models compatible con OpenAI."""
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            req = urllib.request.Request(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            with safe_urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            count = len(data.get("data") or [])
            return TestResult(True, f"OK — {count} modelos disponibles")
        except urllib.error.HTTPError as e:
            return TestResult(False, "Error de autenticación", cls._http_error_msg(e))
        except Exception as e:
            return TestResult(False, "Error de conexión", str(e))

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
            "category": p.category,
            "fields": [f.__dict__ for f in p.fields],
        }
        for p in _REGISTRY.values()
    ]
