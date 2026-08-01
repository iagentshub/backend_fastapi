"""Tests del borrado suave de agentes: activate/deactivate + bloqueo de uso."""

from __future__ import annotations

import asyncio


def _login(client, username: str) -> str:
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@act.test"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _create_agent(client, name="Agente"):
    r = client.post("/api/agents", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_new_agent_is_active_by_default(client):
    _login(client, "act_default")
    a = _create_agent(client)
    assert a["is_active"] is True
    assert a["deactivated_at"] is None


def test_deactivate_hides_from_list(client):
    _login(client, "act_hide")
    a = _create_agent(client, "Ocultable")
    assert client.post(f"/api/agents/{a['id']}/deactivate").status_code == 200

    ids = [x["id"] for x in client.get("/api/agents").json()]
    assert a["id"] not in ids

    ids_incl = [x["id"] for x in client.get("/api/agents?include_inactive=true").json()]
    assert a["id"] in ids_incl


def test_deactivated_agent_chat_returns_409(client):
    _login(client, "act_chat")
    a = _create_agent(client, "Sin Chat")
    client.post(f"/api/agents/{a['id']}/deactivate")

    r = client.post(
        f"/api/agents/{a['id']}/chat", json={"messages": [{"role": "user", "content": "hola"}]}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "resource_inactive"


def test_reactivate_restores(client):
    _login(client, "act_restore")
    a = _create_agent(client, "Restaurable")
    client.post(f"/api/agents/{a['id']}/deactivate")
    r = client.post(f"/api/agents/{a['id']}/activate")
    assert r.status_code == 200
    assert r.json()["is_active"] is True

    ids = [x["id"] for x in client.get("/api/agents").json()]
    assert a["id"] in ids


def test_deactivate_foreign_agent_forbidden(client):
    _login(client, "act_alice")
    a = _create_agent(client, "De Alice")
    _login(client, "act_bob")
    r = client.post(f"/api/agents/{a['id']}/deactivate")
    assert r.status_code in (403, 404)
