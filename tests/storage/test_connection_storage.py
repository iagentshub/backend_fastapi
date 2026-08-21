"""Tests de ConnectionStorage."""
from __future__ import annotations

import pytest

from app.storage.connection_storage import ConnectionStorage


@pytest.fixture()
def storage(patch_data_dir):
    return ConnectionStorage()


async def test_list_empty(storage):
    assert await storage.list() == []


async def test_save_and_list(storage):
    conn = await storage.save({"type": "openai", "label": "Mi OpenAI", "api_key": "sk-test"})
    assert "id" in conn
    items = await storage.list()
    assert len(items) == 1
    assert items[0]["type"] == "openai"


async def test_get_by_id(storage):
    conn = await storage.save({"type": "openai", "api_key": "sk-test"})
    found = await storage.get(conn["id"])
    assert found is not None
    assert found["id"] == conn["id"]


async def test_get_nonexistent(storage):
    assert await storage.get("ghost-id") is None


async def test_save_updates_existing(storage):
    conn = await storage.save({"type": "openai", "label": "V1", "api_key": "sk-test"})
    conn["label"] = "V2"
    updated = await storage.save(conn)
    assert updated["label"] == "V2"
    assert len(await storage.list()) == 1


async def test_save_preserves_created_at(storage):
    conn = await storage.save({"type": "openai", "api_key": "sk-test"})
    original_created = conn["created_at"]
    conn["label"] = "Changed"
    updated = await storage.save(conn)
    assert updated["created_at"] == original_created


async def test_provider_account_is_relational_and_survives_normal_edit(storage):
    conn = await storage.save(
        {
            "type": "openai",
            "api_key": "sk-test",
            "_account_id": "account-1",
            "provider_account_id": "account-1",
        }
    )
    assert (await storage.get(conn["id"]))["provider_account_id"] == "account-1"

    updated = await storage.save(
        {"id": conn["id"], "type": "openai", "name": "Renamed"}
    )
    assert updated["provider_account_id"] == "account-1"
    assert (await storage.get(conn["id"]))["_account_id"] == "account-1"


async def test_delete_existing(storage):
    conn = await storage.save({"type": "openai", "api_key": "sk-test"})
    assert await storage.delete(conn["id"], "admin") is True
    assert await storage.list() == []


async def test_delete_nonexistent(storage):
    assert await storage.delete("ghost-id", "admin") is False


async def test_delete_rejects_missing_owner(storage):
    with pytest.raises(ValueError, match="delete_as_admin"):
        await storage.delete("ghost-id", None)


async def test_delete_as_admin_keeps_explicit_admin_bypass(storage):
    conn = await storage.save(
        {"type": "openai", "api_key": "sk-test"}, owner_id="alice"
    )
    assert await storage.delete_as_admin(conn["id"]) is True


async def test_add_tokens_accumulates(storage):
    conn = await storage.save({"type": "openai", "api_key": "sk-test"})
    await storage.add_tokens(conn["id"], 10, 5)
    await storage.add_tokens(conn["id"], 3, 2)
    updated = await storage.get(conn["id"])
    assert updated["tokens_in"] == 13
    assert updated["tokens_out"] == 7


async def test_add_tokens_nonexistent_id(storage):
    await storage.save({"type": "openai", "api_key": "sk-test"})
    await storage.add_tokens("ghost-id", 10, 5)
    assert len(await storage.list()) == 1


async def test_add_tokens_preserves_api_key(storage):
    conn = await storage.save({"type": "openai", "api_key": "sk-secret"})
    await storage.add_tokens(conn["id"], 7, 3)
    updated = await storage.get(conn["id"])
    assert updated["api_key"] == "sk-secret"


async def test_new_connection_has_no_token_fields(storage):
    conn = await storage.save({"type": "openai", "api_key": "sk-test"})
    assert "tokens_in" not in conn
    assert "tokens_out" not in conn


# ── owner_id isolation ──────────────────────────────────────────────────────

async def test_owner_isolation_list(storage):
    await storage.save({"type": "openai", "api_key": "sk-alice"}, owner_id="alice")
    await storage.save({"type": "anthropic", "api_key": "sk-bob"}, owner_id="bob")
    assert len(await storage.list("alice")) == 1
    assert len(await storage.list("bob")) == 1
    assert len(await storage.list(None)) == 2  # admin ve todo


async def test_owner_isolation_get(storage):
    conn = await storage.save({"type": "openai", "api_key": "sk-alice"}, owner_id="alice")
    assert await storage.get(conn["id"], "alice") is not None
    assert await storage.get(conn["id"], "bob") is None
    assert await storage.get(conn["id"], None) is not None  # admin


async def test_owner_isolation_delete(storage):
    conn = await storage.save({"type": "openai", "api_key": "sk-alice"}, owner_id="alice")
    assert await storage.delete(conn["id"], "bob") is False
    assert await storage.delete(conn["id"], "alice") is True
    assert await storage.list("alice") == []
