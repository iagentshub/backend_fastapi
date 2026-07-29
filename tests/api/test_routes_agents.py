"""Tests CRUD de agentes: GET, POST, DELETE /api/agents."""
from __future__ import annotations

_AGENT_PAYLOAD = {
    "name": "Test Agent",
    "system_prompt": "Eres un asistente de pruebas.",
    "model": "gpt-4o",
    "temperature": 0.5,
}


def test_list_agents_empty(admin_client):
    r = admin_client.get("/api/agents")
    assert r.status_code == 200
    assert r.json() == []


def test_create_agent(admin_client):
    r = admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Agent"
    assert "id" in data


def test_list_agents_after_create(admin_client):
    admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    r = admin_client.get("/api/agents")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_get_agent_by_id(admin_client):
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.get(f"/api/agents/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_agent_not_found(admin_client):
    r = admin_client.get("/api/agents/nonexistent-id")
    assert r.status_code == 404


def test_delete_agent(admin_client):
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.delete(f"/api/agents/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_agent_not_found(admin_client):
    r = admin_client.delete("/api/agents/ghost-agent")
    assert r.status_code == 404


def test_agents_requires_auth(client):
    r = client.get("/api/agents")
    assert r.status_code == 401


def test_create_agent_stores_owner_id(admin_client):
    r = admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    assert r.status_code == 200
    assert r.json()["owner_id"] == "testadmin"
