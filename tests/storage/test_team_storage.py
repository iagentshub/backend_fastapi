"""Tests de TeamStorage."""
from __future__ import annotations

import pytest

from app.storage.teams import TeamStorage


@pytest.fixture()
def ts(tmp_path):
    return TeamStorage(tmp_path / "test.db")


# ── Equipos ────────────────────────────────────────────────────────────────────

def test_create_and_get_team(ts):
    team = ts.create_team("Alfa", "alice")
    assert team["name"] == "Alfa"
    assert team["created_by"] == "alice"
    assert "id" in team

    fetched = ts.get_team(team["id"])
    assert fetched is not None
    assert fetched["name"] == "Alfa"


def test_get_nonexistent_team(ts):
    assert ts.get_team("ghost-id") is None


def test_rename_team(ts):
    team = ts.create_team("Viejo", "alice")
    ts.rename_team(team["id"], "Nuevo")
    assert ts.get_team(team["id"])["name"] == "Nuevo"


def test_delete_team(ts):
    team = ts.create_team("Temporal", "alice")
    ts.delete_team(team["id"])
    assert ts.get_team(team["id"]) is None


def test_list_teams_for_user(ts):
    t1 = ts.create_team("Equipo1", "alice")
    t2 = ts.create_team("Equipo2", "bob")
    alice_teams = ts.list_teams_for_user("alice")
    bob_teams = ts.list_teams_for_user("bob")
    assert any(t["id"] == t1["id"] for t in alice_teams)
    assert not any(t["id"] == t1["id"] for t in bob_teams)
    assert any(t["id"] == t2["id"] for t in bob_teams)


def test_create_team_adds_creator_as_manager(ts):
    team = ts.create_team("X", "alice")
    assert ts.is_manager(team["id"], "alice")


# ── Miembros ───────────────────────────────────────────────────────────────────

def test_add_and_list_members(ts):
    team = ts.create_team("T", "alice")
    ts.add_member(team["id"], "bob", is_manager=False)
    members = ts.list_members(team["id"])
    usernames = [m["username"] for m in members]
    assert "alice" in usernames
    assert "bob" in usernames


def test_remove_member(ts):
    team = ts.create_team("T", "alice")
    ts.add_member(team["id"], "bob")
    ts.remove_member(team["id"], "bob")
    members = ts.list_members(team["id"])
    assert all(m["username"] != "bob" for m in members)


def test_update_member_permissions(ts):
    team = ts.create_team("T", "alice")
    ts.add_member(team["id"], "bob")
    perms = {"agents": {"default": "deny", "items": {"agent-1": {"use": True}}}}
    ts.update_member_permissions(team["id"], "bob", perms)
    member = ts.get_member(team["id"], "bob")
    assert member["permissions"]["agents"]["items"]["agent-1"]["use"] is True


def test_update_member_role(ts):
    team = ts.create_team("T", "alice")
    ts.add_member(team["id"], "bob", is_manager=False)
    assert not ts.is_manager(team["id"], "bob")
    ts.update_member_role(team["id"], "bob", is_manager=True)
    assert ts.is_manager(team["id"], "bob")


def test_is_manager_false_for_non_member(ts):
    team = ts.create_team("T", "alice")
    assert not ts.is_manager(team["id"], "charlie")


def test_isolation_between_teams(ts):
    t1 = ts.create_team("T1", "alice")
    t2 = ts.create_team("T2", "bob")
    ts.add_member(t1["id"], "charlie")
    assert ts.get_member(t2["id"], "charlie") is None


# ── Invitaciones ───────────────────────────────────────────────────────────────

def test_create_and_get_invitation(ts):
    team = ts.create_team("T", "alice")
    token = ts.create_invitation(team["id"], "bob@x.com", "alice")
    inv = ts.get_invitation(token)
    assert inv is not None
    assert inv["invited_email"] == "bob@x.com"
    assert inv["status"] == "pending"


def test_list_invitations(ts):
    team = ts.create_team("T", "alice")
    ts.create_invitation(team["id"], "a@x.com", "alice")
    ts.create_invitation(team["id"], "b@x.com", "alice")
    invs = ts.list_invitations(team["id"])
    assert len(invs) == 2


def test_list_invitations_for_email(ts):
    t1 = ts.create_team("T1", "alice")
    t2 = ts.create_team("T2", "bob")
    ts.create_invitation(t1["id"], "carol@x.com", "alice")
    ts.create_invitation(t2["id"], "carol@x.com", "bob")
    ts.create_invitation(t1["id"], "other@x.com", "alice")
    carol_invs = ts.list_invitations_for_email("carol@x.com")
    assert len(carol_invs) == 2


def test_consume_invitation_accept(ts):
    team = ts.create_team("T", "alice")
    token = ts.create_invitation(team["id"], "bob@x.com", "alice")
    result = ts.consume_invitation(token, accept=True)
    assert result is not None
    assert result["status"] == "accepted"
    # Should not be in pending list anymore
    assert ts.list_invitations(team["id"]) == []


def test_consume_invitation_reject(ts):
    team = ts.create_team("T", "alice")
    token = ts.create_invitation(team["id"], "bob@x.com", "alice")
    result = ts.consume_invitation(token, accept=False)
    assert result["status"] == "rejected"


def test_consume_already_consumed_returns_none(ts):
    team = ts.create_team("T", "alice")
    token = ts.create_invitation(team["id"], "bob@x.com", "alice")
    ts.consume_invitation(token, accept=True)
    assert ts.consume_invitation(token, accept=True) is None


def test_cancel_invitation(ts):
    team = ts.create_team("T", "alice")
    token = ts.create_invitation(team["id"], "bob@x.com", "alice")
    ts.cancel_invitation(token)
    assert ts.get_invitation(token) is None


# ── Enforcement ────────────────────────────────────────────────────────────────

def test_check_resource_permission_deny_by_default(ts):
    team = ts.create_team("T", "alice")
    ts.add_member(team["id"], "bob")
    # No permissions set → default deny
    assert not ts.check_resource_permission(team["id"], "bob", "agents", "agent-1", "use")


def test_check_resource_permission_explicit_allow(ts):
    team = ts.create_team("T", "alice")
    ts.add_member(team["id"], "bob")
    perms = {"agents": {"default": "deny", "items": {"agent-1": {"use": True}}}}
    ts.update_member_permissions(team["id"], "bob", perms)
    assert ts.check_resource_permission(team["id"], "bob", "agents", "agent-1", "use")
    assert not ts.check_resource_permission(team["id"], "bob", "agents", "agent-2", "use")


def test_check_resource_permission_allow_default(ts):
    team = ts.create_team("T", "alice")
    ts.add_member(team["id"], "bob")
    perms = {"agents": {"default": "allow", "items": {}}}
    ts.update_member_permissions(team["id"], "bob", perms)
    assert ts.check_resource_permission(team["id"], "bob", "agents", "any-agent", "use")


# ── Sharing de skills ──────────────────────────────────────────────────────────

def test_share_skill_and_retrieve(ts):
    team = ts.create_team("Dev", "alice")
    ts.share_resource("skill", "my-skill-id", team["id"], "alice")
    entries = ts.get_resource_teams("skill", "my-skill-id")
    assert len(entries) == 1
    assert entries[0]["team_id"] == team["id"]


def test_get_user_shared_skill_ids(ts):
    team = ts.create_team("Dev", "alice")
    ts.add_member(team["id"], "bob")
    ts.share_resource("skill", "my-skill-id", team["id"], "alice")
    shared = ts.get_user_shared_resource_ids("bob", "skill")
    assert "my-skill-id" in shared


def test_unshare_skill(ts):
    team = ts.create_team("Dev", "alice")
    ts.share_resource("skill", "my-skill-id", team["id"], "alice")
    ts.unshare_resource("skill", "my-skill-id", team["id"])
    entries = ts.get_resource_teams("skill", "my-skill-id")
    assert entries == []
