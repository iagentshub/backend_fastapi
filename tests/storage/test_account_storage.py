"""Tests de AccountStorage — SQLite con owner_id."""
from __future__ import annotations

import pytest

from app.storage.accounts import AccountStorage


@pytest.fixture()
async def storage(patch_data_dir):
    from app.config.data import DB_FILE
    return AccountStorage(DB_FILE)


async def test_list_empty(storage):
    assert await storage.list("alice") == []


async def test_save_and_list(storage):
    await storage.save("openai", {"api_key": "sk-test"}, owner_id="alice")
    items = await storage.list("alice")
    assert len(items) == 1
    assert items[0]["provider"] == "openai"
    assert "api_key" not in items[0]
    assert "api_key_masked" in items[0]


async def test_owner_isolation(storage):
    await storage.save("openai", {"api_key": "sk-alice"}, owner_id="alice")
    await storage.save("openai", {"api_key": "sk-bob"}, owner_id="bob")
    assert len(await storage.list("alice")) == 1
    assert len(await storage.list("bob")) == 1
    assert (await storage.get("openai", "alice"))["api_key"] == "sk-alice"
    assert (await storage.get("openai", "bob"))["api_key"] == "sk-bob"


async def test_get_nonexistent(storage):
    assert await storage.get("openai", "alice") is None


async def test_delete(storage):
    await storage.save("openai", {"api_key": "sk-test"}, owner_id="alice")
    assert await storage.delete("openai", "alice") is True
    assert await storage.get("openai", "alice") is None


async def test_delete_nonexistent(storage):
    assert await storage.delete("ghost", "alice") is False


async def test_delete_does_not_affect_other_owner(storage):
    await storage.save("openai", {"api_key": "sk-alice"}, owner_id="alice")
    await storage.save("openai", {"api_key": "sk-bob"}, owner_id="bob")
    await storage.delete("openai", "alice")
    assert await storage.get("openai", "bob") is not None


async def test_save_preserves_api_key(storage):
    await storage.save("openai", {"api_key": "sk-original"}, owner_id="alice")
    await storage.save("openai", {"name": "updated"}, owner_id="alice")
    assert (await storage.get("openai", "alice"))["api_key"] == "sk-original"


async def test_save_preserves_linked_at(storage):
    await storage.save("openai", {"api_key": "sk-test"}, owner_id="alice")
    original_linked_at = (await storage.get("openai", "alice"))["linked_at"]
    await storage.save("openai", {"api_key": "sk-new"}, owner_id="alice")
    assert (await storage.get("openai", "alice"))["linked_at"] == original_linked_at
