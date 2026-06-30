"""Tests de AgentStorage."""

from __future__ import annotations

import asyncio

import pytest

from app.storage.storage import AgentStorage


@pytest.fixture()
def storage(tmp_path, patch_data_dir):  # noqa: ARG001
    return AgentStorage(tmp_path / "agents")


_AGENT = {
    "name": "Mi Agente",
    "system_prompt": "Eres útil.",
    "model": "gpt-4o",
    "temperature": 0.7,
}


def test_list_empty(storage):
    assert asyncio.run(storage.list()) == []


def test_save_and_list(storage):
    agent = asyncio.run(storage.save(_AGENT))
    assert agent["name"] == "Mi Agente"
    assert "id" in agent
    items = asyncio.run(storage.list())
    assert len(items) == 1


def test_save_generates_slug_id(storage):
    agent = asyncio.run(storage.save({"name": "Mi Agente Test"}))
    assert agent["id"] == "mi-agente-test"


def test_save_requires_name(storage):
    with pytest.raises(ValueError, match="name"):
        asyncio.run(storage.save({"name": ""}))


def test_get_by_id(storage):
    agent = asyncio.run(storage.save(_AGENT))
    found = asyncio.run(storage.get(agent["id"]))
    assert found is not None
    assert found["id"] == agent["id"]


def test_get_nonexistent(storage):
    assert asyncio.run(storage.get("ghost-agent")) is None


def test_save_updates_existing(storage):
    agent = asyncio.run(storage.save(_AGENT))
    agent["system_prompt"] = "Nuevo prompt."
    updated = asyncio.run(storage.save(agent))
    assert updated["system_prompt"] == "Nuevo prompt."
    assert len(asyncio.run(storage.list())) == 1


def test_save_preserves_created_at(storage):
    agent = asyncio.run(storage.save(_AGENT))
    original_created = agent["created_at"]
    agent["description"] = "desc actualizada"
    updated = asyncio.run(storage.save(agent))
    assert updated["created_at"] == original_created


def test_delete_existing(storage):
    agent = asyncio.run(storage.save(_AGENT))
    assert asyncio.run(storage.delete(agent["id"])) is True
    assert asyncio.run(storage.list()) == []


def test_delete_nonexistent(storage):
    assert asyncio.run(storage.delete("ghost-agent")) is False


def test_save_stores_owner_id(storage):
    agent = asyncio.run(storage.save(_AGENT, owner_id="alice"))
    assert agent["owner_id"] == "alice"


def test_save_preserves_owner_id_on_update(storage):
    """Al actualizar con owner_id distinto, se usa el nuevo owner_id."""
    agent = asyncio.run(storage.save(_AGENT, owner_id="alice"))
    agent["description"] = "updated"
    updated = asyncio.run(storage.save(agent, owner_id="alice"))
    assert updated["owner_id"] == "alice"


def test_save_sets_owner_id_when_previously_missing(storage):
    """Sin owner_id se asigna 'admin'; al actualizar con owner se aplica."""
    agent = asyncio.run(storage.save(_AGENT))
    assert agent["owner_id"] == "admin"
    agent["description"] = "updated"
    updated = asyncio.run(storage.save(agent, owner_id="alice"))
    assert updated["owner_id"] == "alice"


def test_summary_includes_owner_id(storage):
    asyncio.run(storage.save(_AGENT, owner_id="alice"))
    listed = asyncio.run(storage.list())
    assert listed[0]["owner_id"] == "alice"
