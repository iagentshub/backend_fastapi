"""Tests de AgentStorage."""

from __future__ import annotations

import asyncio
import re

import pytest

from app.storage.agent_storage import AgentStorage


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


def test_save_generates_unique_alphanumeric_id(storage):
    """El id no debe derivar del nombre: dos agentes con el mismo nombre
    (incluso de dueños distintos) deben recibir ids distintos, para que
    no puedan colisionar en la clave primaria (id, owner_id)."""
    a1 = asyncio.run(storage.save({"name": "Mi Agente Test"}, owner_id="alice"))
    a2 = asyncio.run(storage.save({"name": "Mi Agente Test"}, owner_id="bob"))
    assert a1["id"] != a2["id"]
    assert a1["id"] != "mi-agente-test"
    assert re.fullmatch(r"[0-9a-f]{12}", a1["id"])


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
    assert asyncio.run(storage.delete(agent["id"], owner_id="admin")) is True
    assert asyncio.run(storage.list()) == []


def test_delete_nonexistent(storage):
    assert asyncio.run(storage.delete("ghost-agent", owner_id="admin")) is False


def test_delete_rejects_missing_owner(storage):
    with pytest.raises(ValueError, match="delete_as_admin"):
        asyncio.run(storage.delete("ghost-agent", owner_id=None))


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


def test_delete_with_owner_id_does_not_touch_other_owner_same_id(storage):
    """Reproduce el hallazgo crítico: si dos dueños llegaran a compartir id
    (p.ej. datos heredados de la generación por slug), borrar el propio no
    debe borrar el ajeno cuando se pasa owner_id."""
    shared_id = "legacy-shared-id"
    asyncio.run(storage.save({"name": "Asistente", "id": shared_id}, owner_id="alice"))
    asyncio.run(storage.save({"name": "Asistente", "id": shared_id}, owner_id="bob"))

    deleted = asyncio.run(storage.delete(shared_id, owner_id="alice"))
    assert deleted is True

    bob_agent = asyncio.run(storage.get(shared_id, owner_id="bob"))
    assert bob_agent is not None
    assert bob_agent["owner_id"] == "bob"


def test_delete_with_owner_id_rejects_non_owner(storage):
    agent = asyncio.run(storage.save(_AGENT, owner_id="alice"))
    deleted = asyncio.run(storage.delete(agent["id"], owner_id="bob"))
    assert deleted is False
    assert asyncio.run(storage.get(agent["id"])) is not None


def test_delete_as_admin_keeps_explicit_admin_bypass(storage):
    """El bypass existe, pero tiene un nombre administrativo explícito."""
    agent = asyncio.run(storage.save(_AGENT, owner_id="alice"))
    assert asyncio.run(storage.delete_as_admin(agent["id"])) is True


def test_add_tokens_with_owner_id_rejects_non_owner(storage):
    agent = asyncio.run(storage.save(_AGENT, owner_id="alice"))
    asyncio.run(storage.add_tokens(agent["id"], 10, 20, owner_id="bob"))
    unchanged = asyncio.run(storage.get(agent["id"]))
    assert unchanged["tokens_in"] == 0
    assert unchanged["tokens_out"] == 0
