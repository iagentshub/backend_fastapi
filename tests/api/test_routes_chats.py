"""Tests de API para /api/chats/ — historial de conversaciones."""

from __future__ import annotations

import asyncio

import pytest

from app.auth.auth import create_token, register_user_email
from app.storage.chat import ChatStorage


def _auth_client(client, username: str):
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return client


def _make_user(username: str, email: str) -> None:
    asyncio.run(register_user_email(username, email, "password123"))


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(client, patch_data_dir):
    _make_user("alice", "alice@test.com")
    return _auth_client(client, "alice")


@pytest.fixture()
def bob(client, patch_data_dir):
    _make_user("bobby", "bob@test.com")
    return _auth_client(client, "bobby")


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


def test_list_conversations_uses_stable_cursor(alice):
    for title in ("Uno", "Dos", "Tres"):
        alice.post("/api/chats/agent-abc", json={"title": title})

    first = alice.get("/api/chats/agent-abc?limit=2")
    cursor = first.headers["x-next-cursor"]
    second = alice.get(
        "/api/chats/agent-abc?limit=2",
        params={"cursor": cursor},
    )

    assert first.headers["x-has-more"] == "true"
    assert second.headers["x-has-more"] == "false"
    assert len(first.json()) == 2
    assert len(second.json()) == 1
    assert {item["id"] for item in first.json()}.isdisjoint(
        item["id"] for item in second.json()
    )


def test_list_conversations_rejects_invalid_cursor(alice):
    response = alice.get("/api/chats/agent-abc?cursor=no-es-un-cursor")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_cursor"


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
    storage = ChatStorage()
    asyncio.run(storage.add_message(conv["id"], "user", "Hola"))
    asyncio.run(storage.add_message(conv["id"], "assistant", "Mundo"))
    r = alice.get(f"/api/chats/agent-abc/{conv['id']}")
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_get_messages_pages_backwards_without_changing_page_order(
    alice, patch_data_dir
):
    conv = alice.post("/api/chats/agent-abc", json={"title": "Test"}).json()
    storage = ChatStorage()
    for content in ("Uno", "Dos", "Tres"):
        asyncio.run(storage.add_message(conv["id"], "user", content))

    first = alice.get(f"/api/chats/agent-abc/{conv['id']}?limit=2")
    second = alice.get(
        f"/api/chats/agent-abc/{conv['id']}",
        params={"limit": 2, "cursor": first.headers["x-next-cursor"]},
    )

    assert [item["content"] for item in first.json()] == ["Dos", "Tres"]
    assert [item["content"] for item in second.json()] == ["Uno"]


def test_get_messages_includes_tokens(alice, patch_data_dir):
    """Los tokens de una respuesta sobreviven a recargar la conversación —
    no solo están disponibles en el evento SSE de la sesión activa."""
    conv = alice.post("/api/chats/agent-abc", json={"title": "Test"}).json()
    storage = ChatStorage()
    asyncio.run(storage.add_message(conv["id"], "user", "Hola"))
    asyncio.run(
        storage.add_message(
            conv["id"], "assistant", "Mundo", tokens_in=15, tokens_out=6
        )
    )
    msgs = alice.get(f"/api/chats/agent-abc/{conv['id']}").json()
    assert msgs[0]["tokens_in"] == 0
    assert msgs[0]["tokens_out"] == 0
    assert msgs[1]["tokens_in"] == 15
    assert msgs[1]["tokens_out"] == 6


def test_get_messages_marks_interrupted_assistant_reply(alice, patch_data_dir):
    conv = alice.post("/api/chats/agent-abc", json={"title": "Test"}).json()
    storage = ChatStorage()
    asyncio.run(
        storage.add_message(
            conv["id"],
            "assistant",
            "Respuesta parcial",
            tokens_in=9,
            tokens_out=4,
            interrupted=True,
            usage_estimated=True,
        )
    )

    message = alice.get(f"/api/chats/agent-abc/{conv['id']}").json()[0]

    assert message["content"] == "Respuesta parcial"
    assert message["interrupted"] is True
    assert message["usage_estimated"] is True


def test_list_conversations_includes_token_totals(alice, patch_data_dir):
    """La lista de conversaciones trae el total de tokens (suma de sus
    mensajes) para poder mostrar consumo por chat sin leer los mensajes."""
    conv = alice.post("/api/chats/agent-abc", json={"title": "Test"}).json()
    storage = ChatStorage()
    asyncio.run(
        storage.add_message(conv["id"], "assistant", "Uno", tokens_in=10, tokens_out=4)
    )
    asyncio.run(
        storage.add_message(conv["id"], "assistant", "Dos", tokens_in=5, tokens_out=2)
    )
    convs = alice.get("/api/chats/agent-abc").json()
    assert len(convs) == 1
    assert convs[0]["tokens_in"] == 15
    assert convs[0]["tokens_out"] == 6


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
        _make_user("iso_alice", "iso_alice@test.com")
        _make_user("iso_bob", "iso_bob@test.com")
        bob_c = _auth_client(c, "iso_bob")
        bob_c.post("/api/chats/agent-shared", json={"title": "Chat de Bob"})

    with TestClient(app) as c2:
        alice_c = _auth_client(c2, "iso_alice")
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
        _make_user("del_alice", "del_alice@test.com")
        _make_user("del_bob", "del_bob@test.com")
        bob_c = _auth_client(c, "del_bob")
        conv_id = bob_c.post(
            "/api/chats/agent-shared", json={"title": "Chat de Bob"}
        ).json()["id"]

    with TestClient(app) as c2:
        alice_c = _auth_client(c2, "del_alice")
        r = alice_c.delete(f"/api/chats/agent-shared/{conv_id}")
        assert r.status_code == 404


def test_isolation_alice_cannot_read_bob_messages(patch_data_dir):
    """Alice intenta leer los mensajes de una conversación de Bob — debe recibir 404."""
    from fastapi.testclient import TestClient

    from app.api.app import create_app

    app = create_app()
    conv_id = None
    with TestClient(app) as c:
        _make_user("msg_alice", "msg_alice@test.com")
        _make_user("msg_bob", "msg_bob@test.com")
        bob_c = _auth_client(c, "msg_bob")
        conv_id = bob_c.post(
            "/api/chats/agent-shared", json={"title": "Chat de Bob"}
        ).json()["id"]

    with TestClient(app) as c2:
        alice_c = _auth_client(c2, "msg_alice")
        r = alice_c.get(f"/api/chats/agent-shared/{conv_id}")
        assert r.status_code == 404
