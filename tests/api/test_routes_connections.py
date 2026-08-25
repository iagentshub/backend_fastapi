"""Tests CRUD de conexiones: GET, POST, DELETE /api/connections."""

from __future__ import annotations

_CONN_PAYLOAD = {
    "type": "openai",
    "label": "Mi OpenAI",
    "api_key": "sk-test-key",
    "model": "gpt-4o",
}


def test_list_providers(admin_client):
    r = admin_client.get("/api/connections/providers")
    assert r.status_code == 200
    providers = r.json()
    assert isinstance(providers, list)
    types = [p["type"] for p in providers]
    assert "openai" in types
    assert "grok" in types
    assert "qwen" in types
    by_type = {provider["type"]: provider for provider in providers}
    assert by_type["openai"]["supports_chat"] is True
    assert by_type["ssh"]["supports_chat"] is False


def test_create_connection(admin_client):
    r = admin_client.post("/api/connections", json=_CONN_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "openai"
    assert "id" in data
    # la api_key no debe devolverse en el listado
    assert "api_key" not in data


def test_list_connections_hides_api_key(admin_client):
    admin_client.post("/api/connections", json=_CONN_PAYLOAD)
    r = admin_client.get("/api/connections")
    assert r.status_code == 200
    for conn in r.json():
        assert "api_key" not in conn


def test_delete_connection(admin_client):
    created = admin_client.post("/api/connections", json=_CONN_PAYLOAD).json()
    r = admin_client.delete(f"/api/connections/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_connection_not_found(admin_client):
    r = admin_client.delete("/api/connections/ghost-conn")
    assert r.status_code == 404


def test_connections_requires_auth(client):
    r = client.get("/api/connections")
    assert r.status_code == 401
