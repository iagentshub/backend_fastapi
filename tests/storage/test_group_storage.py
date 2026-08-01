"""Tests unitarios de GroupStorage."""
from __future__ import annotations

import pytest

from app.storage.groups import GroupStorage


@pytest.fixture()
async def group(patch_data_dir) -> GroupStorage:
    """GroupStorage con BD aislada por test."""
    from app.config.data import DB_FILE
    return GroupStorage(DB_FILE)


# ── CRUD de groups ────────────────────────────────────────────────────────

async def test_create_and_get(group: GroupStorage):
    created = await group.create("Equipo Alpha", created_by="alice")
    assert created["id"]
    assert created["name"] == "Equipo Alpha"
    assert created["created_by"] == "alice"
    assert created["role"] == "owner"

    fetched = await group.get(created["id"])
    assert fetched is not None
    assert fetched["name"] == "Equipo Alpha"


async def test_get_nonexistent_returns_none(group: GroupStorage):
    assert await group.get("no-existe") is None


async def test_update_name(group: GroupStorage):
    created = await group.create("Nombre Original", created_by="alice")
    ok = await group.update(created["id"], "Nombre Nuevo")
    assert ok is True
    assert (await group.get(created["id"]))["name"] == "Nombre Nuevo"


async def test_update_nonexistent_returns_false(group: GroupStorage):
    assert await group.update("fantasma", "Nuevo") is False


async def test_delete(group: GroupStorage):
    created = await group.create("Para Borrar", created_by="alice")
    group_id = created["id"]
    assert await group.delete(group_id) is True
    assert await group.get(group_id) is None


async def test_delete_removes_members(group: GroupStorage):
    created = await group.create("Grupo con miembros", created_by="alice")
    group_id = created["id"]
    await group.add_member(group_id, "bob", "member")
    await group.delete(group_id)
    assert await group.list_members(group_id) == []


# ── Miembros ──────────────────────────────────────────────────────────────────

async def test_creator_is_automatically_owner(group: GroupStorage):
    from app.auth.auth import get_user_by_username, register_user
    await register_user("alice", "pass1234", email="alice@group.test")
    user = await get_user_by_username("alice")
    assert user is not None
    created = await group.create("Grupo", created_by=user["id"])
    members = await group.list_members(created["id"])
    assert len(members) == 1
    assert members[0]["username"] == "alice"
    assert members[0]["role"] == "owner"


async def test_add_member(group: GroupStorage):
    from app.auth.auth import get_user_by_username, register_user
    await register_user("alice", "pass1234", email="alice@group.test")
    await register_user("bobby", "pass1234", email="bobby@group.test")
    owner = await get_user_by_username("alice")
    member = await get_user_by_username("bobby")
    assert owner is not None and member is not None
    created = await group.create("Grupo", created_by=owner["id"])
    ok = await group.add_member(created["id"], member["id"], "member")
    assert ok is True
    members = await group.list_members(created["id"])
    usernames = {m["username"] for m in members}
    assert "bobby" in usernames


async def test_add_member_invalid_role(group: GroupStorage):
    created = await group.create("Grupo", created_by="alice")
    ok = await group.add_member(created["id"], "bob", "superuser")
    assert ok is False


async def test_remove_member(group: GroupStorage):
    created = await group.create("Grupo", created_by="alice")
    await group.add_member(created["id"], "bob", "member")
    ok = await group.remove_member(created["id"], "bob")
    assert ok is True
    members = await group.list_members(created["id"])
    assert not any(m["username"] == "bob" for m in members)


async def test_remove_nonexistent_member(group: GroupStorage):
    created = await group.create("Grupo", created_by="alice")
    ok = await group.remove_member(created["id"], "fantasma")
    assert ok is False


async def test_update_member_role(group: GroupStorage):
    created = await group.create("Grupo", created_by="alice")
    await group.add_member(created["id"], "bob", "member")
    ok = await group.update_member_role(created["id"], "bob", "admin")
    assert ok is True
    member = await group.get_member(created["id"], "bob")
    assert member["role"] == "admin"


async def test_update_role_invalid(group: GroupStorage):
    created = await group.create("Grupo", created_by="alice")
    await group.add_member(created["id"], "bob", "member")
    ok = await group.update_member_role(created["id"], "bob", "superadmin")
    assert ok is False


async def test_is_member_true(group: GroupStorage):
    created = await group.create("Grupo", created_by="alice")
    await group.add_member(created["id"], "bob", "member")
    assert await group.is_member(created["id"], "bob") is True


async def test_is_member_false(group: GroupStorage):
    created = await group.create("Grupo", created_by="alice")
    assert await group.is_member(created["id"], "eve") is False


async def test_list_for_user(group: GroupStorage):
    group1 = await group.create("Grupo 1", created_by="alice")
    group2 = await group.create("Grupo 2", created_by="charlie")
    await group.add_member(group2["id"], "alice", "member")
    group3 = await group.create("Grupo 3", created_by="bob")

    alice_groups = await group.list_for_user("alice")
    ids = {w["id"] for w in alice_groups}
    assert group1["id"] in ids
    assert group2["id"] in ids
    assert group3["id"] not in ids


# ── Autorización ──────────────────────────────────────────────────────────────

async def test_can_access_personal_group(group: GroupStorage):
    assert await group.can_access("alice", "alice") is True


async def test_can_access_team_group_as_member(group: GroupStorage):
    created = await group.create("Equipo", created_by="alice")
    await group.add_member(created["id"], "bob", "member")
    assert await group.can_access(created["id"], "bob") is True


async def test_can_access_team_group_not_member(group: GroupStorage):
    created = await group.create("Equipo", created_by="alice")
    assert await group.can_access(created["id"], "eve") is False


async def test_can_manage_personal_group(group: GroupStorage):
    assert await group.can_manage("alice", "alice") is True


async def test_can_manage_as_owner(group: GroupStorage):
    created = await group.create("Equipo", created_by="alice")
    assert await group.can_manage(created["id"], "alice") is True


async def test_can_manage_as_admin_member(group: GroupStorage):
    created = await group.create("Equipo", created_by="alice")
    await group.add_member(created["id"], "bob", "admin")
    assert await group.can_manage(created["id"], "bob") is True


async def test_cannot_manage_as_plain_member(group: GroupStorage):
    created = await group.create("Equipo", created_by="alice")
    await group.add_member(created["id"], "bob", "member")
    assert await group.can_manage(created["id"], "bob") is False


async def test_cannot_manage_if_not_member(group: GroupStorage):
    created = await group.create("Equipo", created_by="alice")
    assert await group.can_manage(created["id"], "eve") is False
