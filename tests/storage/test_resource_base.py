"""Tests de ResourceStorage: set_active y sincronización de etiquetas comunes."""

from __future__ import annotations

import asyncio

import pytest

from app.storage.storage import AgentStorage, SkillStorage


@pytest.fixture()
def agents(tmp_path, patch_data_dir):  # noqa: ARG001
    return AgentStorage(tmp_path / "agents")


@pytest.fixture()
def skills(tmp_path, patch_data_dir):  # noqa: ARG001
    return SkillStorage(tmp_path / "skills")


def _is_active(store, resource_id, owner_id):
    async def _run():
        from app.storage.db import open_db

        async with open_db() as conn:
            row = await conn.fetchone(
                f"SELECT is_active, deactivated_at FROM {store.table} WHERE id=? AND owner_id=?",
                (resource_id, owner_id),
            )
            return row

    return asyncio.run(_run())


def test_set_active_deactivates_own_resource(agents):
    a = asyncio.run(agents.save({"name": "Desactivable"}, owner_id="alice"))
    assert asyncio.run(agents.set_active(a["id"], "alice", False)) is True
    row = _is_active(agents, a["id"], "alice")
    assert row["is_active"] == 0
    assert row["deactivated_at"] is not None


def test_set_active_reactivates(agents):
    a = asyncio.run(agents.save({"name": "Reactivable"}, owner_id="alice"))
    asyncio.run(agents.set_active(a["id"], "alice", False))
    assert asyncio.run(agents.set_active(a["id"], "alice", True)) is True
    row = _is_active(agents, a["id"], "alice")
    assert row["is_active"] == 1
    assert row["deactivated_at"] is None


def test_set_active_rejects_non_owner(agents):
    a = asyncio.run(agents.save({"name": "De Alice"}, owner_id="alice"))
    assert asyncio.run(agents.set_active(a["id"], "bob", False)) is False
    row = _is_active(agents, a["id"], "alice")
    assert row["is_active"] == 1


def test_set_active_admin_bypass(agents):
    a = asyncio.run(agents.save({"name": "De Alice"}, owner_id="alice"))
    assert asyncio.run(agents.set_active(a["id"], None, False)) is True
    row = _is_active(agents, a["id"], "alice")
    assert row["is_active"] == 0


def test_set_active_missing_returns_false(skills):
    assert asyncio.run(skills.set_active("noexiste", "alice", False)) is False
