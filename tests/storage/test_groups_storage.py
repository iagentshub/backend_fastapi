"""Tests unitarios de GroupStorage."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture()
def storage(patch_data_dir):
    from app.config.data import DB_FILE
    from app.storage.groups import GroupStorage
    return GroupStorage(DB_FILE)


# ── Grupos CRUD ────────────────────────────────────────────────────────────────

def test_create_group(storage):
    g = asyncio.run(storage.create("ws1", "Alpha", "admin"))
    assert g["name"] == "Alpha"
    assert g["workspace_id"] == "ws1"
    assert "id" in g


def test_get_group(storage):
    g = asyncio.run(storage.create("ws1", "Beta", "admin"))
    found = asyncio.run(storage.get(g["id"]))
    assert found is not None
    assert found["name"] == "Beta"


def test_get_group_inexistente(storage):
    assert asyncio.run(storage.get("fantasma")) is None


def test_list_for_workspace(storage):
    asyncio.run(storage.create("ws2", "G1", "admin"))
    asyncio.run(storage.create("ws2", "G2", "admin"))
    grupos = asyncio.run(storage.list_for_workspace("ws2"))
    nombres = [g["name"] for g in grupos]
    assert "G1" in nombres
    assert "G2" in nombres


def test_list_for_workspace_vacio(storage):
    grupos = asyncio.run(storage.list_for_workspace("ws_vacio"))
    assert grupos == []


def test_rename_group(storage):
    g = asyncio.run(storage.create("ws1", "Viejo", "admin"))
    ok = asyncio.run(storage.rename(g["id"], "Nuevo"))
    assert ok is True
    found = asyncio.run(storage.get(g["id"]))
    assert found["name"] == "Nuevo"


def test_delete_group(storage):
    g = asyncio.run(storage.create("ws1", "ParaBorrar", "admin"))
    ok = asyncio.run(storage.delete(g["id"]))
    assert ok is True
    assert asyncio.run(storage.get(g["id"])) is None


def test_create_duplicate_raises(storage):
    asyncio.run(storage.create("ws1", "Unico", "admin"))
    with pytest.raises(ValueError, match="Ya existe"):
        asyncio.run(storage.create("ws1", "Unico", "admin"))


# ── Miembros ───────────────────────────────────────────────────────────────────

def test_add_and_list_members(storage):
    g = asyncio.run(storage.create("ws3", "ConMiembros", "admin"))
    asyncio.run(storage.add_member(g["id"], "ws3", "alice"))
    asyncio.run(storage.add_member(g["id"], "ws3", "bob"))
    members = asyncio.run(storage.list_members(g["id"]))
    usernames = [m["username"] for m in members]
    assert "alice" in usernames
    assert "bob" in usernames


def test_add_member_idempotente(storage):
    g = asyncio.run(storage.create("ws3", "IdempotGrp", "admin"))
    asyncio.run(storage.add_member(g["id"], "ws3", "carol"))
    asyncio.run(storage.add_member(g["id"], "ws3", "carol"))
    members = asyncio.run(storage.list_members(g["id"]))
    assert len([m for m in members if m["username"] == "carol"]) == 1


def test_is_member(storage):
    g = asyncio.run(storage.create("ws3", "IsMemberGrp", "admin"))
    asyncio.run(storage.add_member(g["id"], "ws3", "dave"))
    assert asyncio.run(storage.is_member(g["id"], "dave")) is True
    assert asyncio.run(storage.is_member(g["id"], "nadie")) is False


def test_remove_member(storage):
    g = asyncio.run(storage.create("ws3", "RemoveMember", "admin"))
    asyncio.run(storage.add_member(g["id"], "ws3", "eve"))
    ok = asyncio.run(storage.remove_member(g["id"], "eve"))
    assert ok is True
    assert asyncio.run(storage.is_member(g["id"], "eve")) is False


def test_remove_member_inexistente(storage):
    g = asyncio.run(storage.create("ws3", "SinEve", "admin"))
    ok = asyncio.run(storage.remove_member(g["id"], "nadie"))
    assert ok is False


# ── Compartición de recursos ───────────────────────────────────────────────────

def test_share_resource(storage):
    g = asyncio.run(storage.create("ws4", "ShareGrp", "admin"))
    ok = asyncio.run(storage.share_resource("connection", "conn-1", g["id"], "admin"))
    assert ok is True


def test_get_resource_groups(storage):
    g = asyncio.run(storage.create("ws4", "ShareGrp2", "admin"))
    asyncio.run(storage.share_resource("connection", "conn-2", g["id"], "admin"))
    groups = asyncio.run(storage.get_resource_groups("connection", "conn-2"))
    assert any(rg["group_id"] == g["id"] for rg in groups)


def test_unshare_resource(storage):
    g = asyncio.run(storage.create("ws4", "UnshareGrp", "admin"))
    asyncio.run(storage.share_resource("agent", "ag-1", g["id"], "admin"))
    ok = asyncio.run(storage.unshare_resource("agent", "ag-1", g["id"]))
    assert ok is True
    groups = asyncio.run(storage.get_resource_groups("agent", "ag-1"))
    assert groups == []


def test_unshare_inexistente(storage):
    g = asyncio.run(storage.create("ws4", "NoShare", "admin"))
    ok = asyncio.run(storage.unshare_resource("agent", "no-existe", g["id"]))
    assert ok is False


def test_get_group_resources(storage):
    g = asyncio.run(storage.create("ws4", "GroupRes", "admin"))
    asyncio.run(storage.share_resource("skill", "sk-1", g["id"], "admin"))
    asyncio.run(storage.share_resource("skill", "sk-2", g["id"], "admin"))
    resources = asyncio.run(storage.get_group_resources(g["id"], "skill"))
    ids = [r["resource_id"] for r in resources]
    assert "sk-1" in ids
    assert "sk-2" in ids


def test_get_user_shared_resource_ids(storage):
    g = asyncio.run(storage.create("ws5", "UserShared", "admin"))
    asyncio.run(storage.add_member(g["id"], "ws5", "frank"))
    asyncio.run(storage.share_resource("knowledge", "kb-1", g["id"], "admin"))
    asyncio.run(storage.share_resource("knowledge", "kb-2", g["id"], "admin"))
    ids = asyncio.run(
        storage.get_user_shared_resource_ids("frank", "knowledge", "ws5")
    )
    assert "kb-1" in ids
    assert "kb-2" in ids


def test_get_user_shared_resource_ids_sin_pertenencia(storage):
    g = asyncio.run(storage.create("ws5", "NotMineGrp", "admin"))
    asyncio.run(storage.share_resource("knowledge", "kb-3", g["id"], "admin"))
    ids = asyncio.run(
        storage.get_user_shared_resource_ids("grace", "knowledge", "ws5")
    )
    assert "kb-3" not in ids
