"""Tests del índice transversal de etiquetas: GET /api/labels/{label}.

Complementa test_routes_labels.py (labels en el blob); aquí se prueba la tabla
resource_labels que enlaza objetos de distinto tipo por una etiqueta común.
"""

from __future__ import annotations

import asyncio


def _login(client, username: str) -> str:
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@lblidx.test"))
    client.cookies.set("ga_token", create_token(username))
    return username


def test_label_links_agent_and_skill_across_types(client):
    _login(client, "lblidx_owner")
    agent = client.post(
        "/api/agents",
        json={"name": "Agente Etiquetado", "labels": ["development"]},
    ).json()
    skill = client.post(
        "/api/skills/private",
        json={
            "name": "Skill Etiquetada",
            "content": "x",
            "labels": ["development"],
        },
    ).json()

    r = client.get("/api/labels/development")
    assert r.status_code == 200
    found = {(x["resource_type"], x["resource_id"]) for x in r.json()}
    assert ("agent", agent["id"]) in found
    assert ("skill", skill["id"]) in found


def test_label_index_updated_on_relabel(client):
    _login(client, "lblidx_relabel")
    agent = client.post(
        "/api/agents", json={"name": "Recategorizable", "labels": ["viejo"]}
    ).json()
    assert any(
        x["resource_id"] == agent["id"] for x in client.get("/api/labels/viejo").json()
    )

    client.post(
        "/api/agents",
        json={"id": agent["id"], "name": "Recategorizable", "labels": ["nuevo"]},
    )
    assert client.get("/api/labels/viejo").json() == []
    assert any(
        x["resource_id"] == agent["id"] for x in client.get("/api/labels/nuevo").json()
    )


def test_label_index_cleared_on_delete(client):
    _login(client, "lblidx_delete")
    agent = client.post(
        "/api/agents", json={"name": "Borrable", "labels": ["temporal"]}
    ).json()
    client.delete(f"/api/agents/{agent['id']}")
    assert client.get("/api/labels/temporal").json() == []


def test_label_scoped_by_owner(client):
    _login(client, "lblidx_alice")
    a = client.post(
        "/api/agents", json={"name": "De Alice", "labels": ["compartida"]}
    ).json()

    _login(client, "lblidx_bob")
    r = client.get("/api/labels/compartida")
    assert all(x["resource_id"] != a["id"] for x in r.json())
