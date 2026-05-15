"""Tests de AgentStorage."""
from __future__ import annotations

import pytest

from app.storage.storage import AgentStorage


@pytest.fixture()
def storage(tmp_path):
    return AgentStorage(tmp_path / "agents")


_AGENT = {
    "name": "Mi Agente",
    "system_prompt": "Eres útil.",
    "model": "gpt-4o",
    "temperature": 0.7,
}


def test_list_empty(storage):
    assert storage.list() == []


def test_save_and_list(storage):
    agent = storage.save(_AGENT)
    assert agent["name"] == "Mi Agente"
    assert "id" in agent
    items = storage.list()
    assert len(items) == 1


def test_save_generates_slug_id(storage):
    agent = storage.save({"name": "Mi Agente Test"})
    assert agent["id"] == "mi-agente-test"


def test_save_requires_name(storage):
    with pytest.raises(ValueError, match="name"):
        storage.save({"name": ""})


def test_get_by_id(storage):
    agent = storage.save(_AGENT)
    found = storage.get(agent["id"])
    assert found is not None
    assert found["id"] == agent["id"]


def test_get_nonexistent(storage):
    assert storage.get("ghost-agent") is None


def test_save_updates_existing(storage):
    agent = storage.save(_AGENT)
    agent["system_prompt"] = "Nuevo prompt."
    updated = storage.save(agent)
    assert updated["system_prompt"] == "Nuevo prompt."
    assert len(storage.list()) == 1


def test_save_preserves_created_at(storage):
    agent = storage.save(_AGENT)
    original_created = agent["created_at"]
    agent["description"] = "desc actualizada"
    updated = storage.save(agent)
    assert updated["created_at"] == original_created


def test_delete_existing(storage):
    agent = storage.save(_AGENT)
    assert storage.delete(agent["id"]) is True
    assert storage.list() == []


def test_delete_nonexistent(storage):
    assert storage.delete("ghost-agent") is False


def test_save_stores_owner_id(storage):
    agent = storage.save(_AGENT, owner_id="alice")
    assert agent["owner_id"] == "alice"


def test_save_preserves_owner_id_on_update(storage):
    agent = storage.save(_AGENT, owner_id="alice")
    agent["description"] = "updated"
    updated = storage.save(agent, owner_id="bob")
    assert updated["owner_id"] == "alice"


def test_save_sets_owner_id_when_previously_missing(storage):
    agent = storage.save(_AGENT)
    assert agent["owner_id"] is None
    agent["description"] = "updated"
    updated = storage.save(agent, owner_id="alice")
    assert updated["owner_id"] == "alice"


def test_summary_includes_owner_id(storage):
    agent = storage.save(_AGENT, owner_id="alice")
    listed = storage.list()
    assert listed[0]["owner_id"] == "alice"
