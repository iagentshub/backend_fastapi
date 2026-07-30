"""Tests de API para /api/chats/ — historial de conversaciones."""
from __future__ import annotations

import asyncio

import pytest

from app.auth.auth import create_token, register_user_email
from app.config.data import DB_FILE
from app.storage.chat import ChatStorage


def _auth_client(client, username: str):
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return client


def _make_user(username: str, email: str) -> None:
    asyncio.run(register_user_email(email, "password123"))


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def alice(client, patch_data_dir):
    _make_user("alice@test.com", "alice@test.com")
    return _auth_client(client, "alice@test.com")


@pytest.fixture()
def bob(client, patch_data_dir):
    _make_user("bob@test.com", "bob@test.com")
    return _auth_client(client, "bob@test.com")


# ── Lista de conversaciones ────────────────────────────────────────────────────

def test_list_conversations_empty(alice):
    r = alice.get("/api/chats/agent-xyz")
    assert r.status_code == 200
    assert r.json() == []


def test_list_conversations_requires_auth(client):
    r = client.get("/api/chats/agent-xyz")
    assert r.status_code == 401


# ── Crear conversación ─────────────────────────────────────────────────────────

def test_create_conversation(alice):
    r = alice.post("/api/chats/agent-abc", json={"title": "Mi chat"})
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Mi chat"
    assert data["agent_id"] == "agent-abc"
    assert "id" in data


def test_list_conversations_after_create(alice):
    alice.post("/api/chats/agent-abc", json={"title": "Primera"})
    alice.post("/api/chats/agent-abc", json={"title": "Segunda"})
    r = alice.get("/api/chats/agent-abc")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_recent_conversations_across_agents(alice):
    alice.post("/api/chats/agent-a", json={"title": "Primera"})
    alice.post("/api/chats/agent-b", json={"title": "Segunda"})

    r = alice.get("/api/chats/recent?limit=1")

    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["agent_id"] == "agent-b"


def test_list_recent_conversations_validates_limit(alice):
    assert alice.get("/api/chats/recent?limit=0").status_code == 422
    assert alice.get("/api/chats/recent?limit=51").status_code == 422


# ── Mensajes ───────────────────────────────────────────────────────────────────

def test_get_messages_empty(alice):
    conv = alice.post("/api/chats/agent-abc", json={"title": ""}).json()
    r = alice.get(f"/api/chats/agent-abc/{conv['id']}")
    assert r.status_code == 200
    assert r.json() == []


def test_get_messages_not_found(alice):
    r = alice.get("/api/chats/agent-abc/no-existe")
    assert r.status_code == 404


def test_get_messages_with_content(alice, patch_data_dir):
    conv = alice.post("/api/chats/agent-abc", json={"title": "Test"}).json()
    storage = ChatStorage(DB_FILE)
    asyncio.run(storage.add_message(conv["id"], "user", "Hola"))
    asyncio.run(storage.add_message(conv["id"], "assistant", "Mundo"))
    r = alice.get(f"/api/chats/agent-abc/{conv['id']}")
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


# ── Borrar conversación ────────────────────────────────────────────────────────

def test_delete_conversation(alice):
    conv = alice.post("/api/chats/agent-abc", json={"title": "Borrar"}).json()
    r = alice.delete(f"/api/chats/agent-abc/{conv['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Ya no aparece en el listado
    assert alice.get("/api/chats/agent-abc").json() == []


def test_delete_conversation_not_found(alice):
    r = alice.delete("/api/chats/agent-abc/no-existe")
    assert r.status_code == 404


# ── Aislamiento entre usuarios ─────────────────────────────────────────────────

def test_isolation_alice_cannot_see_bob_conversations(patch_data_dir):
    """Bob crea una conversación; Alice no debe verla (clientes separados)."""
    from fastapi.testclient import TestClient

    from app.api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        _make_user("iso_alice@test.com", "iso_alice@test.com")
        _make_user("iso_bob@test.com", "iso_bob@test.com")
        bob_c = _auth_client(c, "iso_bob@test.com")
        bob_c.post("/api/chats/agent-shared", json={"title": "Chat de Bob"})

    with TestClient(app) as c2:
        alice_c = _auth_client(c2, "iso_alice@test.com")
        r = alice_c.get("/api/chats/agent-shared")
        assert r.status_code == 200
        assert r.json() == []


def test_isolation_alice_cannot_delete_bob_conversation(patch_data_dir):
    """Alice intenta borrar una conversación de Bob — debe recibir 404."""
    from fastapi.testclient import TestClient

    from app.api.app import create_app

    app = create_app()
    conv_id = None
    with TestClient(app) as c:
        _make_user("del_alice@test.com", "del_alice@test.com")
        _make_user("del_bob@test.com", "del_bob@test.com")
        bob_c = _auth_client(c, "del_bob@test.com")
        conv_id = bob_c.post("/api/chats/agent-shared", json={"title": "Chat de Bob"}).json()["id"]

    with TestClient(app) as c2:
        alice_c = _auth_client(c2, "del_alice@test.com")
        r = alice_c.delete(f"/api/chats/agent-shared/{conv_id}")
        assert r.status_code == 404


def test_isolation_alice_cannot_read_bob_messages(patch_data_dir):
    """Alice intenta leer los mensajes de una conversación de Bob — debe recibir 404."""
    from fastapi.testclient import TestClient

    from app.api.app import create_app

    app = create_app()
    conv_id = None
    with TestClient(app) as c:
        _make_user("msg_alice@test.com", "msg_alice@test.com")
        _make_user("msg_bob@test.com", "msg_bob@test.com")
        bob_c = _auth_client(c, "msg_bob@test.com")
        conv_id = bob_c.post("/api/chats/agent-shared", json={"title": "Chat de Bob"}).json()["id"]

    with TestClient(app) as c2:
        alice_c = _auth_client(c2, "msg_alice@test.com")
        r = alice_c.get(f"/api/chats/agent-shared/{conv_id}")
        assert r.status_code == 404
