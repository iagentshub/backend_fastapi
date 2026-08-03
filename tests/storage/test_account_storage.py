"""Tests de AccountStorage — SQLite con owner_id, varias cuentas por provider."""
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
    saved = await storage.save({"provider": "openai", "api_key": "sk-test"}, owner_id="alice")
    assert saved["id"]
    items = await storage.list("alice")
    assert len(items) == 1
    assert items[0]["provider"] == "openai"
    assert "api_key" not in items[0]
    assert "api_key_masked" in items[0]


async def test_save_generates_new_id_each_time_without_id(storage):
    """Sin `id` en el payload, cada save crea una cuenta nueva — permite
    varias del mismo provider para el mismo owner."""
    a = await storage.save({"provider": "openai", "api_key": "sk-one"}, owner_id="alice")
    b = await storage.save({"provider": "openai", "api_key": "sk-two"}, owner_id="alice")
    assert a["id"] != b["id"]
    assert len(await storage.list("alice")) == 2


async def test_save_with_id_updates_existing(storage):
    created = await storage.save({"provider": "openai", "api_key": "sk-original"}, owner_id="alice")
    updated = await storage.save({"id": created["id"], "api_key": "sk-new"}, owner_id="alice")
    assert updated["id"] == created["id"]
    items = await storage.list("alice")
    assert len(items) == 1


async def test_owner_isolation(storage):
    a = await storage.save({"provider": "openai", "api_key": "sk-alice"}, owner_id="alice")
    b = await storage.save({"provider": "openai", "api_key": "sk-bob"}, owner_id="bob")
    assert len(await storage.list("alice")) == 1
    assert len(await storage.list("bob")) == 1
    assert (await storage.get(a["id"], "alice"))["api_key"] == "sk-alice"
    assert (await storage.get(b["id"], "bob"))["api_key"] == "sk-bob"


async def test_get_nonexistent(storage):
    assert await storage.get("does-not-exist", "alice") is None


async def test_delete(storage):
    created = await storage.save({"provider": "openai", "api_key": "sk-test"}, owner_id="alice")
    assert await storage.delete(created["id"], "alice") is True
    assert await storage.get(created["id"], "alice") is None


async def test_delete_nonexistent(storage):
    assert await storage.delete("ghost", "alice") is False


async def test_delete_does_not_affect_other_owner(storage):
    a = await storage.save({"provider": "openai", "api_key": "sk-alice"}, owner_id="alice")
    b = await storage.save({"provider": "openai", "api_key": "sk-bob"}, owner_id="bob")
    await storage.delete(a["id"], "alice")
    assert await storage.get(b["id"], "bob") is not None


async def test_delete_does_not_affect_sibling_same_provider(storage):
    """Borrar una cuenta no afecta a otra del mismo provider y owner."""
    a = await storage.save({"provider": "openai", "api_key": "sk-one"}, owner_id="alice")
    b = await storage.save({"provider": "openai", "api_key": "sk-two"}, owner_id="alice")
    await storage.delete(a["id"], "alice")
    assert await storage.get(b["id"], "alice") is not None
    assert len(await storage.list("alice")) == 1


async def test_save_preserves_api_key_when_omitted(storage):
    created = await storage.save({"provider": "openai", "api_key": "sk-original"}, owner_id="alice")
    await storage.save({"id": created["id"], "name": "updated"}, owner_id="alice")
    assert (await storage.get(created["id"], "alice"))["api_key"] == "sk-original"


async def test_save_preserves_linked_at(storage):
    created = await storage.save({"provider": "openai", "api_key": "sk-test"}, owner_id="alice")
    original_linked_at = (await storage.get(created["id"], "alice"))["linked_at"]
    await storage.save({"id": created["id"], "api_key": "sk-new"}, owner_id="alice")
    assert (await storage.get(created["id"], "alice"))["linked_at"] == original_linked_at
