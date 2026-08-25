"""Agentes, conexiones, conocimiento, skills y memoria del panel admin."""

from __future__ import annotations

from tests.api.admin._helpers import _AGENT_PAYLOAD, _insert_connection, _register

# ── Admin agents ──────────────────────────────────────────────────────────────


def test_admin_list_agents(admin_client):
    admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    r = admin_client.get("/api/admin/agents")
    assert r.status_code == 200
    agents = r.json()
    assert isinstance(agents, list)
    assert any(a["name"] == "Admin Test Agent" for a in agents)


def test_admin_list_agents_has_owner_username(admin_client):
    admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    agents = admin_client.get("/api/admin/agents").json()
    private = [a for a in agents if a.get("scope") == "private"]
    assert private, "se esperaba al menos un agente privado"
    assert private[0]["owner_username"] == "testadmin"


def test_admin_delete_agent(admin_client):
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.delete(f"/api/admin/agents/{created['id']}?scope=private")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    agents = admin_client.get("/api/admin/agents").json()
    assert not any(a["id"] == created["id"] for a in agents)


def test_admin_delete_agent_not_found(admin_client):
    r = admin_client.delete("/api/admin/agents/ghost-agent?scope=private")
    assert r.status_code == 404


def test_admin_delete_public_agent(admin_client):
    """El admin puede borrar agentes públicos (antes daba 500: eran de solo
    lectura y el ValueError no se capturaba en la ruta admin)."""
    created = admin_client.post(
        "/api/agents", json={**_AGENT_PAYLOAD, "scope": "public"}
    ).json()
    r = admin_client.delete(f"/api/admin/agents/{created['id']}?scope=public")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    agents = admin_client.get("/api/admin/agents").json()
    assert not any(a["id"] == created["id"] for a in agents)


def test_admin_set_agent_owner(admin_client):
    _register("new_owner_a1")
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.put(
        f"/api/admin/resources/agent/{created['id']}/owner",
        json={"username": "new_owner_a1"},
    )
    assert r.status_code == 200
    agents = admin_client.get("/api/admin/agents").json()
    moved = next(a for a in agents if a["id"] == created["id"])
    import asyncio

    from app.auth.auth import get_user_by_username

    assert moved["owner_id"] == asyncio.run(get_user_by_username("new_owner_a1"))["id"]


def test_admin_set_owner_unknown_user_returns_404(admin_client):
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.put(
        f"/api/admin/resources/agent/{created['id']}/owner",
        json={"owner_id": "ghost_user_xyz"},
    )
    assert r.status_code == 404


def test_admin_set_owner_inactive_user_returns_400(admin_client):
    _register("new_owner_a3")
    admin_client.patch("/api/admin/users/new_owner_a3", json={"is_active": False})
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.put(
        f"/api/admin/resources/agent/{created['id']}/owner",
        json={"owner_id": "new_owner_a3"},
    )
    assert r.status_code == 400


def test_admin_set_owner_invalid_resource_type_returns_422(admin_client):
    _register("new_owner_a2")
    r = admin_client.put(
        "/api/admin/resources/bogus/some-id/owner",
        json={"owner_id": "new_owner_a2"},
    )
    assert r.status_code == 422


def test_admin_reviews_and_quarantines_tools_with_existing_labels(admin_client):
    tool = admin_client.post(
        "/api/tools/private",
        json={"name": "Tool revisable", "language": "python", "content": "print(1)"},
    ).json()
    detail = admin_client.get(f"/api/admin/tools/{tool['id']}")
    assert detail.status_code == 200
    assert detail.json()["content"] == "print(1)"
    assert "binary_b64" not in detail.json()

    quarantined = admin_client.put(
        f"/api/admin/tools/{tool['id']}/security",
        json={"state": "quarantine"},
    )
    assert quarantined.status_code == 200, quarantined.text
    assert "quarantine" in quarantined.json()["labels"]

    approved = admin_client.put(
        f"/api/admin/tools/{tool['id']}/security",
        json={"state": "approved"},
    )
    assert approved.status_code == 200, approved.text
    assert "quarantine" not in approved.json()["labels"]
    assert "review" not in approved.json()["labels"]

    invalid = admin_client.put(
        f"/api/admin/tools/{tool['id']}/security",
        json={"state": "invented"},
    )
    assert invalid.status_code == 422


def test_admin_agents_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser",
            "email": "std@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/agents")
    assert r.status_code == 403


# ── Admin connections ─────────────────────────────────────────────────────────


def test_admin_list_connections(admin_client):
    _insert_connection()
    r = admin_client.get("/api/admin/connections")
    assert r.status_code == 200
    conns = r.json()
    assert isinstance(conns, list)
    assert len(conns) >= 1
    assert conns[0]["supports_chat"] is True
    assert conns[0]["is_active"] is True
    assert conns[0]["model"] == "gpt-4o"


def test_admin_list_connections_has_owner_username(admin_client):
    _insert_connection()
    conns = admin_client.get("/api/admin/connections").json()
    assert conns[0]["owner_username"] == "testadmin"


def test_admin_delete_connection(admin_client):
    conn_id = _insert_connection()
    r = admin_client.delete(f"/api/admin/connections/{conn_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    conns = admin_client.get("/api/admin/connections").json()
    assert not any(c["id"] == conn_id for c in conns)


def test_admin_delete_connection_not_found(admin_client):
    r = admin_client.delete("/api/admin/connections/ghost-conn")
    assert r.status_code == 404


def test_admin_connections_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser2",
            "email": "std2@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/connections")
    assert r.status_code == 403


# ── Admin knowledge ───────────────────────────────────────────────────────────


def test_admin_list_knowledge(admin_client):
    r = admin_client.get("/api/admin/knowledge")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_knowledge_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser3",
            "email": "std3@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/knowledge")
    assert r.status_code == 403


def test_admin_list_skills(admin_client):
    r = admin_client.get("/api/admin/skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_skills_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser4",
            "email": "std4@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/skills")
    assert r.status_code == 403


def test_admin_delete_skill(admin_client):
    skill = admin_client.post(
        "/api/skills/private",
        json={
            "name": "Admin delete me",
            "description": "temp",
            "content": "do the thing",
        },
    ).json()

    r = admin_client.delete(f"/api/admin/skills/{skill['id']}")

    assert r.status_code == 200
    remaining = admin_client.get("/api/admin/skills").json()
    assert skill["id"] not in {item["id"] for item in remaining}


def test_admin_delete_skill_not_found(admin_client):
    r = admin_client.delete("/api/admin/skills/missing")
    assert r.status_code == 404


def test_admin_list_memory(admin_client):
    r = admin_client.get("/api/admin/memory")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_memory_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser5",
            "email": "std5@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/memory")
    assert r.status_code == 403


def test_admin_delete_memory(admin_client):
    admin_client.post("/api/memory/admin-delete-me", json={"content": "some notes"})

    memory = admin_client.get("/api/admin/memory").json()
    entry = next(m for m in memory if m["filename"] == "admin-delete-me")

    r = admin_client.delete(f"/api/admin/memory/{entry['id']}")

    assert r.status_code == 200
    remaining = admin_client.get("/api/admin/memory").json()
    assert entry["id"] not in {m["id"] for m in remaining}


def test_admin_delete_memory_invalid_id(admin_client):
    r = admin_client.delete("/api/admin/memory/no-separator")
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "item_id"


def test_admin_delete_memory_not_found(admin_client):
    r = admin_client.delete("/api/admin/memory/testadmin::missing")
    assert r.status_code == 404
