"""Tests unitarios de WorkspaceStorage."""
from __future__ import annotations

import pytest
from pathlib import Path

from app.storage.workspaces import WorkspaceStorage


@pytest.fixture()
async def ws(patch_data_dir) -> WorkspaceStorage:
    """WorkspaceStorage con BD aislada por test."""
    from app.config.data import DB_FILE
    return WorkspaceStorage(DB_FILE)


# ── CRUD de workspaces ────────────────────────────────────────────────────────

async def test_create_and_get(ws: WorkspaceStorage):
    created = await ws.create("Equipo Alpha", created_by="alice")
    assert created["id"]
    assert created["name"] == "Equipo Alpha"
    assert created["created_by"] == "alice"
    assert created["role"] == "owner"

    fetched = await ws.get(created["id"])
    assert fetched is not None
    assert fetched["name"] == "Equipo Alpha"


async def test_get_nonexistent_returns_none(ws: WorkspaceStorage):
    assert await ws.get("no-existe") is None


async def test_update_name(ws: WorkspaceStorage):
    created = await ws.create("Nombre Original", created_by="alice")
    ok = await ws.update(created["id"], "Nombre Nuevo")
    assert ok is True
    assert (await ws.get(created["id"]))["name"] == "Nombre Nuevo"


async def test_update_nonexistent_returns_false(ws: WorkspaceStorage):
    assert await ws.update("fantasma", "Nuevo") is False


async def test_delete(ws: WorkspaceStorage):
    created = await ws.create("Para Borrar", created_by="alice")
    ws_id = created["id"]
    assert await ws.delete(ws_id) is True
    assert await ws.get(ws_id) is None


async def test_delete_removes_members(ws: WorkspaceStorage):
    created = await ws.create("WS con miembros", created_by="alice")
    ws_id = created["id"]
    await ws.add_member(ws_id, "bob", "member")
    await ws.delete(ws_id)
    assert await ws.list_members(ws_id) == []


# ── Miembros ──────────────────────────────────────────────────────────────────

async def test_creator_is_automatically_owner(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    members = await ws.list_members(created["id"])
    assert len(members) == 1
    assert members[0]["username"] == "alice"
    assert members[0]["role"] == "owner"


async def test_add_member(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    ok = await ws.add_member(created["id"], "bob", "member")
    assert ok is True
    members = await ws.list_members(created["id"])
    usernames = {m["username"] for m in members}
    assert "bob" in usernames


async def test_add_member_invalid_role(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    ok = await ws.add_member(created["id"], "bob", "superuser")
    assert ok is False


async def test_remove_member(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    await ws.add_member(created["id"], "bob", "member")
    ok = await ws.remove_member(created["id"], "bob")
    assert ok is True
    members = await ws.list_members(created["id"])
    assert not any(m["username"] == "bob" for m in members)


async def test_remove_nonexistent_member(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    ok = await ws.remove_member(created["id"], "fantasma")
    assert ok is False


async def test_update_member_role(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    await ws.add_member(created["id"], "bob", "member")
    ok = await ws.update_member_role(created["id"], "bob", "admin")
    assert ok is True
    member = await ws.get_member(created["id"], "bob")
    assert member["role"] == "admin"


async def test_update_role_invalid(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    await ws.add_member(created["id"], "bob", "member")
    ok = await ws.update_member_role(created["id"], "bob", "superadmin")
    assert ok is False


async def test_is_member_true(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    await ws.add_member(created["id"], "bob", "member")
    assert await ws.is_member(created["id"], "bob") is True


async def test_is_member_false(ws: WorkspaceStorage):
    created = await ws.create("WS", created_by="alice")
    assert await ws.is_member(created["id"], "eve") is False


async def test_list_for_user(ws: WorkspaceStorage):
    ws1 = await ws.create("WS 1", created_by="alice")
    ws2 = await ws.create("WS 2", created_by="charlie")
    await ws.add_member(ws2["id"], "alice", "member")
    ws3 = await ws.create("WS 3", created_by="bob")

    alice_ws = await ws.list_for_user("alice")
    ids = {w["id"] for w in alice_ws}
    assert ws1["id"] in ids
    assert ws2["id"] in ids
    assert ws3["id"] not in ids


# ── Autorización ──────────────────────────────────────────────────────────────

async def test_can_access_personal_workspace(ws: WorkspaceStorage):
    assert await ws.can_access("alice", "alice") is True


async def test_can_access_team_workspace_as_member(ws: WorkspaceStorage):
    created = await ws.create("Equipo", created_by="alice")
    await ws.add_member(created["id"], "bob", "member")
    assert await ws.can_access(created["id"], "bob") is True


async def test_can_access_team_workspace_not_member(ws: WorkspaceStorage):
    created = await ws.create("Equipo", created_by="alice")
    assert await ws.can_access(created["id"], "eve") is False


async def test_can_manage_personal_workspace(ws: WorkspaceStorage):
    assert await ws.can_manage("alice", "alice") is True


async def test_can_manage_as_owner(ws: WorkspaceStorage):
    created = await ws.create("Equipo", created_by="alice")
    assert await ws.can_manage(created["id"], "alice") is True


async def test_can_manage_as_admin_member(ws: WorkspaceStorage):
    created = await ws.create("Equipo", created_by="alice")
    await ws.add_member(created["id"], "bob", "admin")
    assert await ws.can_manage(created["id"], "bob") is True


async def test_cannot_manage_as_plain_member(ws: WorkspaceStorage):
    created = await ws.create("Equipo", created_by="alice")
    await ws.add_member(created["id"], "bob", "member")
    assert await ws.can_manage(created["id"], "bob") is False


async def test_cannot_manage_if_not_member(ws: WorkspaceStorage):
    created = await ws.create("Equipo", created_by="alice")
    assert await ws.can_manage(created["id"], "eve") is False
