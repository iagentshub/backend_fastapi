"""Tests de /api/sharing."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _auth(client: TestClient, username: str, password: str = "pass1234") -> TestClient:
    from app.auth.auth import create_token, register_user
    try:
        register_user(username, password, email=f"{username}@test.com")
    except ValueError:
        pass
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return client


def _create_skill(client: TestClient, name: str | None = None) -> str:
    """Save a private skill and return its id."""
    name = name or ("skill-" + _uid())
    r = client.post("/api/skills/private", json={
        "name": name, "description": "desc", "content": "body", "icon": "🔧",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _create_team(client: TestClient, name: str | None = None) -> str:
    name = name or ("team-" + _uid())
    r = client.post("/api/teams/", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ── Compartir skill ────────────────────────────────────────────────────────────

def test_share_skill_valid(client):
    _auth(client, "alice")
    skill_id = _create_skill(client)
    team_id = _create_team(client)

    r = client.post(f"/api/sharing/skill/{skill_id}", json={"team_id": team_id})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_share_skill_shows_in_get(client):
    _auth(client, "alice")
    skill_id = _create_skill(client)
    team_id = _create_team(client)

    client.post(f"/api/sharing/skill/{skill_id}", json={"team_id": team_id})

    r = client.get(f"/api/sharing/skill/{skill_id}")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["team_id"] == team_id


def test_unshare_skill(client):
    _auth(client, "alice")
    skill_id = _create_skill(client)
    team_id = _create_team(client)

    client.post(f"/api/sharing/skill/{skill_id}", json={"team_id": team_id})
    r = client.delete(f"/api/sharing/skill/{skill_id}/{team_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.get(f"/api/sharing/skill/{skill_id}")
    assert r2.json() == []


def test_share_skill_invalid_type(client):
    _auth(client, "alice")
    r = client.post("/api/sharing/badtype/some-id", json={"team_id": "x"})
    assert r.status_code == 422


def test_share_skill_not_owner(client, tmp_path):
    """User B cannot share a skill owned by user A."""
    from app.api.app import create_app
    from app.auth.auth import create_token, register_user

    app = create_app()
    alice_c = TestClient(app, raise_server_exceptions=True)
    bob_c = TestClient(app, raise_server_exceptions=True)

    _auth(alice_c, "alice_b")
    _auth(bob_c, "bob_b")

    skill_id = _create_skill(alice_c, "Alice Skill")
    team_id = _create_team(alice_c, "Alice Team")

    r = bob_c.post(f"/api/sharing/skill/{skill_id}", json={"team_id": team_id})
    assert r.status_code == 403


def test_share_requires_auth(client):
    r = client.post("/api/sharing/skill/some-id", json={"team_id": "x"})
    assert r.status_code == 401


# ── Regresión: agentes siguen funcionando ──────────────────────────────────────

def test_share_agent_regression(client):
    _auth(client, "alice-agent-" + _uid())
    team_id = _create_team(client)

    r = client.post("/api/agents", json={
        "name": "Test Agent " + _uid(), "agent_type": "generic",
        "description": "", "system_prompt": "",
    })
    assert r.status_code == 200, r.text
    agent_id = r.json()["id"]

    r = client.post(f"/api/sharing/agent/{agent_id}", json={"team_id": team_id})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── Skill compartida aparece en list para miembro del equipo ──────────────────

def test_shared_skill_visible_to_team_member(client):
    """Shared skill appears in team member's list with _shared=True."""
    u = _uid()
    alice_name = f"alice-{u}"
    bob_name = f"bob-{u}"

    _auth(client, alice_name)
    skill_id = _create_skill(client)
    team_id = _create_team(client)

    # Add bob as member via the same singleton the routes use
    from app.auth.auth import register_user, create_token
    import app.api.routes.teams as teams_mod
    register_user(bob_name, "pass1234", email=f"{bob_name}@test.com")
    teams_mod._ts.add_member(team_id, bob_name)

    # Alice shares the skill
    r = client.post(f"/api/sharing/skill/{skill_id}", json={"team_id": team_id})
    assert r.status_code == 200

    # Now authenticate as bob on the same client and list skills
    token = create_token(bob_name)
    client.cookies.set("ga_token", token)
    r = client.get("/api/skills?scope=all")
    assert r.status_code == 200
    skills = r.json()
    ids = [s["id"] for s in skills]
    assert skill_id in ids
    shared_flags = [s.get("_shared") for s in skills if s["id"] == skill_id]
    assert shared_flags[0] is True
