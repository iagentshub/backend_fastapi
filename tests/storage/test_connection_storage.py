"""Tests de ConnectionStorage."""
from __future__ import annotations


import pytest

from app.storage.storage import ConnectionStorage


@pytest.fixture()
def storage(tmp_path):
    return ConnectionStorage(tmp_path / "test.db")


def test_list_empty(storage):
    assert storage.list() == []


def test_save_and_list(storage):
    conn = storage.save({"type": "openai", "label": "Mi OpenAI", "api_key": "sk-test"})
    assert "id" in conn
    items = storage.list()
    assert len(items) == 1
    assert items[0]["type"] == "openai"


def test_get_by_id(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-test"})
    found = storage.get(conn["id"])
    assert found is not None
    assert found["id"] == conn["id"]


def test_get_nonexistent(storage):
    assert storage.get("ghost-id") is None


def test_save_updates_existing(storage):
    conn = storage.save({"type": "openai", "label": "V1", "api_key": "sk-test"})
    conn["label"] = "V2"
    updated = storage.save(conn)
    assert updated["label"] == "V2"
    assert len(storage.list()) == 1


def test_save_preserves_created_at(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-test"})
    original_created = conn["created_at"]
    conn["label"] = "Changed"
    updated = storage.save(conn)
    assert updated["created_at"] == original_created


def test_delete_existing(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-test"})
    assert storage.delete(conn["id"]) is True
    assert storage.list() == []


def test_delete_nonexistent(storage):
    assert storage.delete("ghost-id") is False


def test_add_tokens_accumulates(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-test"})
    storage.add_tokens(conn["id"], 10, 5)
    storage.add_tokens(conn["id"], 3, 2)
    updated = storage.get(conn["id"])
    assert updated["tokens_in"] == 13
    assert updated["tokens_out"] == 7


def test_add_tokens_nonexistent_id(storage):
    storage.save({"type": "openai", "api_key": "sk-test"})
    storage.add_tokens("ghost-id", 10, 5)
    assert len(storage.list()) == 1


def test_add_tokens_preserves_api_key(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-secret"})
    storage.add_tokens(conn["id"], 7, 3)
    updated = storage.get(conn["id"])
    assert updated["api_key"] == "sk-secret"


def test_new_connection_has_no_token_fields(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-test"})
    assert "tokens_in" not in conn
    assert "tokens_out" not in conn


# ── owner_id isolation ──────────────────────────────────────────────────────

def test_owner_isolation_list(storage):
    storage.save({"type": "openai", "api_key": "sk-alice"}, owner_id="alice")
    storage.save({"type": "anthropic", "api_key": "sk-bob"}, owner_id="bob")
    assert len(storage.list("alice")) == 1
    assert len(storage.list("bob")) == 1
    assert len(storage.list(None)) == 2  # admin ve todo


def test_owner_isolation_get(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-alice"}, owner_id="alice")
    assert storage.get(conn["id"], "alice") is not None
    assert storage.get(conn["id"], "bob") is None
    assert storage.get(conn["id"], None) is not None  # admin


def test_owner_isolation_delete(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-alice"}, owner_id="alice")
    assert storage.delete(conn["id"], "bob") is False
    assert storage.delete(conn["id"], "alice") is True
    assert storage.list("alice") == []
