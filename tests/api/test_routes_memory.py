"""Tests de memoria de agentes: GET, POST, DELETE /api/memory."""
from __future__ import annotations


def test_list_memory_empty(admin_client):
    r = admin_client.get("/api/memory")
    assert r.status_code == 200
    assert r.json() == []


def test_save_and_get_memory(admin_client):
    r = admin_client.post("/api/memory/agent_test.md", json={"content": "Recuerdo algo importante."})
    assert r.status_code == 200

    r = admin_client.get("/api/memory/agent_test.md")
    assert r.status_code == 200
    assert r.json()["content"] == "Recuerdo algo importante."


def test_get_memory_not_found(admin_client):
    r = admin_client.get("/api/memory/nonexistent.md")
    assert r.status_code == 404


def test_delete_memory(admin_client):
    admin_client.post("/api/memory/to_delete.md", json={"content": "temp"})
    r = admin_client.delete("/api/memory/to_delete.md")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_memory_not_found(admin_client):
    r = admin_client.delete("/api/memory/ghost.md")
    assert r.status_code == 404


def test_memory_requires_auth(client):
    r = client.get("/api/memory")
    assert r.status_code == 401
