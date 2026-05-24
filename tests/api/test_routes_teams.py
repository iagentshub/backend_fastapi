"""Tests de /api/teams."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _register_and_auth(client: TestClient, email: str, password: str = "pass1234") -> TestClient:
    """Register a user and set auth cookie."""
    from app.auth.auth import create_token, register_user
    username = email
    register_user(username, password, email=email)
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return client


def _make_client_for(tmp_path, email: str) -> TestClient:
    """Isolated client authenticated as email."""
    from app.api.app import create_app
    from app.auth.auth import create_token, register_user
    app = create_app()
    c = TestClient(app, raise_server_exceptions=True)
    try:
        register_user(email, "pass1234", email=email)
    except ValueError:
        pass
    token = create_token(email)
    c.cookies.set("ga_token", token)
    return c


# ── Crear equipo ───────────────────────────────────────────────────────────────

def test_create_team_promotes_to_gestor(client):
    _register_and_auth(client, "creator@x.com")
    r = client.post("/api/teams/", json={"name": "Mi Equipo"})
    assert r.status_code == 200
    team = r.json()
    assert team["name"] == "Mi Equipo"
    # Check role changed
    me = client.get("/api/auth/me").json()
    assert me["role"] == "gestor"
    assert me["manages_teams"] is True


def test_create_team_requires_name(client):
    _register_and_auth(client, "u2@x.com")
    r = client.post("/api/teams/", json={"name": ""})
    assert r.status_code == 400


def test_create_team_unauthenticated(client):
    r = client.post("/api/teams/", json={"name": "X"})
    assert r.status_code == 401


# ── Listar equipos ─────────────────────────────────────────────────────────────

def test_list_teams_empty(client):
    _register_and_auth(client, "empty@x.com")
    r = client.get("/api/teams/")
    assert r.status_code == 200
    assert r.json() == []


def test_list_teams_shows_own_team(client):
    _register_and_auth(client, "owner@x.com")
    client.post("/api/teams/", json={"name": "Alpha"})
    r = client.get("/api/teams/")
    teams = r.json()
    assert any(t["name"] == "Alpha" for t in teams)


# ── Invitaciones ───────────────────────────────────────────────────────────────

def test_send_invitation_and_accept(client):
    from app.auth.auth import register_user

    # Creator creates team
    _register_and_auth(client, "mgr@x.com")
    team_r = client.post("/api/teams/", json={"name": "Squad"})
    team_id = team_r.json()["id"]

    # Register invitee
    register_user("invitee@x.com", "pass1234", email="invitee@x.com")

    # Send invitation
    r = client.post(f"/api/teams/{team_id}/invitations", json={"email": "invitee@x.com"})
    assert r.status_code == 200
    token = r.json()["token"]

    # Switch to invitee
    from app.auth.auth import create_token
    client.cookies.set("ga_token", create_token("invitee@x.com"))

    # Check pending invitations
    pending = client.get("/api/teams/invitations/pending").json()
    assert any(inv["id"] == token for inv in pending)

    # Accept
    accept_r = client.post(f"/api/teams/invitations/{token}/accept")
    assert accept_r.status_code == 200
    assert accept_r.json()["status"] == "accepted"

    # Should now be in team members (switch back to manager)
    client.cookies.set("ga_token", create_token("mgr@x.com"))
    members = client.get(f"/api/teams/{team_id}/members").json()
    assert any(m["username"] == "invitee@x.com" for m in members)


def test_send_invitation_and_reject(client):
    from app.auth.auth import create_token, register_user

    _register_and_auth(client, "mgr2@x.com")
    team_id = client.post("/api/teams/", json={"name": "TeamB"}).json()["id"]
    register_user("user2@x.com", "pass1234", email="user2@x.com")
    token = client.post(f"/api/teams/{team_id}/invitations", json={"email": "user2@x.com"}).json()["token"]

    client.cookies.set("ga_token", create_token("user2@x.com"))
    r = client.post(f"/api/teams/invitations/{token}/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    # Should not be a member
    client.cookies.set("ga_token", create_token("mgr2@x.com"))
    members = client.get(f"/api/teams/{team_id}/members").json()
    assert not any(m["username"] == "user2@x.com" for m in members)


def test_non_manager_cannot_send_invitation(client):
    from app.auth.auth import create_token, register_user

    _register_and_auth(client, "mgr3@x.com")
    team_id = client.post("/api/teams/", json={"name": "TeamC"}).json()["id"]

    # Register a member (not manager)
    register_user("member3@x.com", "pass1234", email="member3@x.com")
    token_member = create_token("member3@x.com")

    # Invite them first via manager
    inv_token = client.post(
        f"/api/teams/{team_id}/invitations", json={"email": "member3@x.com"}
    ).json()["token"]
    client.cookies.set("ga_token", token_member)
    client.post(f"/api/teams/invitations/{inv_token}/accept")

    # Now member tries to send an invitation (should fail)
    r = client.post(f"/api/teams/{team_id}/invitations", json={"email": "other@x.com"})
    assert r.status_code == 403


# ── Eliminar equipo → demote gestor ───────────────────────────────────────────

def test_delete_team_demotes_gestor(client):
    from app.auth.auth import get_user_role

    _register_and_auth(client, "ex_gestor@x.com")
    team_id = client.post("/api/teams/", json={"name": "Temp"}).json()["id"]
    assert get_user_role("ex_gestor@x.com") == "gestor"

    r = client.delete(f"/api/teams/{team_id}")
    assert r.status_code == 200
    assert get_user_role("ex_gestor@x.com") == "standard"


# ── Editar permisos ────────────────────────────────────────────────────────────

def test_update_member_permissions(client):
    from app.auth.auth import create_token, register_user

    _register_and_auth(client, "gestor_p@x.com")
    team_id = client.post("/api/teams/", json={"name": "Perms"}).json()["id"]

    register_user("mem_p@x.com", "pass1234", email="mem_p@x.com")
    inv_token = client.post(
        f"/api/teams/{team_id}/invitations", json={"email": "mem_p@x.com"}
    ).json()["token"]
    client.cookies.set("ga_token", create_token("mem_p@x.com"))
    client.post(f"/api/teams/invitations/{inv_token}/accept")

    client.cookies.set("ga_token", create_token("gestor_p@x.com"))
    perms = {"agents": {"default": "deny", "items": {"a1": {"use": True}}}}
    r = client.patch(f"/api/teams/{team_id}/members/mem_p@x.com", json={"permissions": perms})
    assert r.status_code == 200
    assert r.json()["permissions"]["agents"]["items"]["a1"]["use"] is True


# ── No-miembro no ve el equipo ─────────────────────────────────────────────────

def test_non_member_cannot_get_team(client):
    from app.auth.auth import create_token, register_user

    _register_and_auth(client, "owner_x@x.com")
    team_id = client.post("/api/teams/", json={"name": "Secret"}).json()["id"]

    register_user("outsider@x.com", "pass1234", email="outsider@x.com")
    client.cookies.set("ga_token", create_token("outsider@x.com"))
    r = client.get(f"/api/teams/{team_id}")
    assert r.status_code == 403
