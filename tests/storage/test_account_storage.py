"""Tests de AccountStorage — SQLite con owner_id."""
from __future__ import annotations

import pytest

from app.storage.accounts import AccountStorage


@pytest.fixture()
def storage(tmp_path):
    return AccountStorage(tmp_path / "test.db")


def test_list_empty(storage):
    assert storage.list("alice") == []


def test_save_and_list(storage):
    storage.save("openai", {"api_key": "sk-test"}, owner_id="alice")
    items = storage.list("alice")
    assert len(items) == 1
    assert items[0]["provider"] == "openai"
    assert "api_key" not in items[0]
    assert "api_key_masked" in items[0]


def test_owner_isolation(storage):
    storage.save("openai", {"api_key": "sk-alice"}, owner_id="alice")
    storage.save("openai", {"api_key": "sk-bob"}, owner_id="bob")
    assert len(storage.list("alice")) == 1
    assert len(storage.list("bob")) == 1
    assert storage.get("openai", "alice")["api_key"] == "sk-alice"
    assert storage.get("openai", "bob")["api_key"] == "sk-bob"


def test_get_nonexistent(storage):
    assert storage.get("openai", "alice") is None


def test_delete(storage):
    storage.save("openai", {"api_key": "sk-test"}, owner_id="alice")
    assert storage.delete("openai", "alice") is True
    assert storage.get("openai", "alice") is None


def test_delete_nonexistent(storage):
    assert storage.delete("ghost", "alice") is False


def test_delete_does_not_affect_other_owner(storage):
    storage.save("openai", {"api_key": "sk-alice"}, owner_id="alice")
    storage.save("openai", {"api_key": "sk-bob"}, owner_id="bob")
    storage.delete("openai", "alice")
    assert storage.get("openai", "bob") is not None


def test_save_preserves_api_key(storage):
    storage.save("openai", {"api_key": "sk-original"}, owner_id="alice")
    storage.save("openai", {"name": "updated"}, owner_id="alice")
    assert storage.get("openai", "alice")["api_key"] == "sk-original"


def test_save_preserves_linked_at(storage):
    storage.save("openai", {"api_key": "sk-test"}, owner_id="alice")
    original_linked_at = storage.get("openai", "alice")["linked_at"]
    storage.save("openai", {"api_key": "sk-new"}, owner_id="alice")
    assert storage.get("openai", "alice")["linked_at"] == original_linked_at
