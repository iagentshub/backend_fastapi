"""Tests para origin_type y preferencias de conexión de agentes."""
from __future__ import annotations

import asyncio

_AGENT_PAYLOAD = {
    "name": "Agent Origin Test",
    "system_prompt": "Test prompt.",
    "model": "gpt-4o",
}


def _register(username: str) -> None:
    from app.auth.auth import register_user

    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass


def _token(username: str) -> str:
    from app.auth.auth import create_token

    return create_token(username)


def _set_cookie(client, username: str) -> None:
    client.cookies.set("ga_token", _token(username))


# ── origin_type: owner ────────────────────────────────────────────────────────

def test_agent_origin_type_owner(client):
    """Crear un agente y recibirlo → origin_type debe ser 'owner'."""
    _register("orig_owner")
    _set_cookie(client, "orig_owner")

    r = client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r.status_code == 200
    created = r.json()

    # Check via list
    r_list = client.get("/api/v2/agents")
    assert r_list.status_code == 200
    agents = r_list.json()["items"]
    my_agent = next((a for a in agents if a["id"] == created["id"]), None)
    assert my_agent is not None
    assert my_agent["origin_type"] == "owner"

    # Check via detail
    r_detail = client.get(f"/api/agents/{created['id']}")
    assert r_detail.status_code == 200
    assert r_detail.json()["origin_type"] == "owner"


# ── origin_type: linked ───────────────────────────────────────────────────────

def test_agent_origin_type_linked(client):
    """Un agente compartido via group aparece con origin_type='linked'."""

    _register("orig_linked_owner")
    _register("orig_linked_member")

    # owner creates group and agent
    _set_cookie(client, "orig_linked_owner")
    r_groups = client.post("/api/groups", json={"name": "Linked Grupo"})
    assert r_groups.status_code == 200
    group_id = r_groups.json()["id"]

    r_agent = client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r_agent.status_code == 200
    agent_id = r_agent.json()["id"]

    # add member directly to group
    r_add = client.post(
        f"/api/groups/{group_id}/members",
        json={"username": "orig_linked_member", "role": "member"},
    )
    assert r_add.status_code == 200

    # owner shares the agent with the group (group_id = group id)
    r_share = client.post(
        f"/api/sharing/agent/{agent_id}",
        json={"group_id": group_id},
    )
    assert r_share.status_code == 200

    # member lists agents → shared agent must have origin_type='linked'
    _set_cookie(client, "orig_linked_member")
    r_list = client.get("/api/v2/agents")
    assert r_list.status_code == 200
    agents = r_list.json()["items"]
    shared = next((a for a in agents if a["id"] == agent_id), None)
    assert shared is not None, "El agente compartido no aparece en el listado del miembro"
    assert shared["origin_type"] == "linked"


# ── preferences: get empty ────────────────────────────────────────────────────

def test_agent_preferences_get_empty(client):
    """GET preferences sin ninguna preferencia guardada → connection_id null."""
    _register("pref_empty_user")
    _set_cookie(client, "pref_empty_user")

    r = client.get("/api/agents/nonexistent-agent-id/preferences")
    assert r.status_code == 200
    assert r.json() == {"connection_id": None}


# ── preferences: put and get ──────────────────────────────────────────────────

def test_agent_preferences_put_and_get(client):
    """PUT una connection_id → GET la devuelve correctamente."""
    _register("pref_rw_user")
    _set_cookie(client, "pref_rw_user")

    r_agent = client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r_agent.status_code == 200
    agent_id = r_agent.json()["id"]

    # set preference
    r_put = client.put(
        f"/api/agents/{agent_id}/preferences",
        json={"connection_id": "conn-abc-123"},
    )
    assert r_put.status_code == 200
    assert r_put.json()["ok"] is True

    # read it back
    r_get = client.get(f"/api/agents/{agent_id}/preferences")
    assert r_get.status_code == 200
    assert r_get.json()["connection_id"] == "conn-abc-123"


def test_agent_preferences_put_null_clears(client):
    """PUT con connection_id null → GET devuelve null."""
    _register("pref_null_user")
    _set_cookie(client, "pref_null_user")

    r_agent = client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r_agent.status_code == 200
    agent_id = r_agent.json()["id"]

    # set then clear
    client.put(f"/api/agents/{agent_id}/preferences", json={"connection_id": "old-conn"})
    r_put = client.put(f"/api/agents/{agent_id}/preferences", json={"connection_id": None})
    assert r_put.status_code == 200

    r_get = client.get(f"/api/agents/{agent_id}/preferences")
    assert r_get.status_code == 200
    assert r_get.json()["connection_id"] is None


# ── preferences: requires auth ────────────────────────────────────────────────

def test_agent_preferences_requires_auth(client):
    """GET y PUT de preferencias sin sesión → 401."""
    r_get = client.get("/api/agents/some-id/preferences")
    assert r_get.status_code == 401

    r_put = client.put("/api/agents/some-id/preferences", json={"connection_id": "x"})
    assert r_put.status_code == 401


# ── edit forbidden for non-owner ──────────────────────────────────────────────

def test_agent_edit_forbidden_for_non_owner(client):
    """Usuario B intenta editar agente de usuario A → 403."""
    _register("edit_owner_a")
    _register("edit_other_b")

    # A creates agent
    _set_cookie(client, "edit_owner_a")
    r = client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r.status_code == 200
    agent = r.json()

    # B tries to update agent with same id
    _set_cookie(client, "edit_other_b")
    r_edit = client.post(
        "/api/agents",
        json={**_AGENT_PAYLOAD, "id": agent["id"], "name": "Hacked Name"},
    )
    assert r_edit.status_code == 403
    assert "propietario" in r_edit.json()["detail"]["message"].lower()


def test_agent_edit_allowed_for_owner(client):
    """El propietario puede editar su propio agente."""
    _register("edit_owner_self")
    _set_cookie(client, "edit_owner_self")

    r = client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r.status_code == 200
    agent = r.json()

    r_edit = client.post(
        "/api/agents",
        json={**_AGENT_PAYLOAD, "id": agent["id"], "name": "Updated Name"},
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["name"] == "Updated Name"


def test_agent_edit_allowed_for_admin(client, admin_client):
    """Admin puede editar cualquier agente sin importar el owner."""
    _register("edit_victim_user")
    _set_cookie(client, "edit_victim_user")

    r = client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r.status_code == 200
    agent = r.json()

    # admin edits it
    r_edit = admin_client.post(
        "/api/agents",
        json={**_AGENT_PAYLOAD, "id": agent["id"], "name": "Admin Updated"},
    )
    assert r_edit.status_code == 200
