"""Tests del registro y tipos base de providers."""
from __future__ import annotations

from app.connections.base import FieldDef, TestResult, all_providers, get_provider


def test_all_providers_registered():
    providers = all_providers()
    types = [p["type"] for p in providers]
    assert "openai" in types
    assert "claude" in types
    assert "gemini" in types
    assert "grok" in types
    assert "qwen" in types
    assert "ollama" in types


def test_get_provider_returns_class():
    cls = get_provider("openai")
    assert cls is not None
    assert cls.type_id == "openai"


def test_get_provider_unknown_returns_none():
    assert get_provider("nonexistent_provider") is None


def test_all_providers_have_required_fields():
    for p in all_providers():
        assert "type" in p
        assert "label" in p
        assert "icon" in p
        assert isinstance(p["fields"], list)


def test_field_def_defaults():
    f = FieldDef(key="api_key", label="API Key")
    assert f.type == "text"
    assert f.required is False
    assert f.options == []


def test_test_result_ok():
    r = TestResult(ok=True, message="OK")
    assert r.ok is True
    assert r.detail == ""


def test_test_result_fail():
    r = TestResult(ok=False, message="Error", detail="timeout")
    assert r.ok is False
    assert r.detail == "timeout"
