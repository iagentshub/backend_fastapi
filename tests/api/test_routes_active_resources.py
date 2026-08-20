"""Borrado suave en recursos operativos: conexiones y workflows."""

from __future__ import annotations

import asyncio


def _login(client, username: str) -> str:
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@actres.test"))
    client.cookies.set("ga_token", create_token(username))
    return username


# ── Connections ─────────────────────────────────────────────────────────────


def test_connection_deactivate_hides_from_list(client):
    _login(client, "actres_conn")
    c = client.post(
        "/api/connections",
        json={"name": "Conn Desactivable", "type": "openai", "api_key": "sk-x"},
    ).json()
    assert c["is_active"] is True

    assert client.post(f"/api/connections/{c['id']}/deactivate").status_code == 200
    ids = [x["id"] for x in client.get("/api/connections").json()]
    assert c["id"] not in ids
    ids_incl = [
        x["id"] for x in client.get("/api/connections?include_inactive=true").json()
    ]
    assert c["id"] in ids_incl


# ── Workflows ───────────────────────────────────────────────────────────────


def _minimal_workflow_definition(agent_id: str) -> dict:
    return {
        "nodes": [{"id": "n1", "type": "agent", "agent_id": agent_id, "data": {}}],
        "edges": [],
    }


def test_workflow_deactivate_blocks_run(client):
    _login(client, "actres_wf")
    agent = client.post("/api/agents", json={"name": "WF Agent"}).json()
    wf = client.post(
        "/api/workflows",
        json={
            "name": "Flujo Desactivable",
            "definition": _minimal_workflow_definition(agent["id"]),
        },
    )
    assert wf.status_code == 200, wf.text
    wf_id = wf.json()["id"]

    assert client.post(f"/api/workflows/{wf_id}/deactivate").status_code == 200
    ids = [w["id"] for w in client.get("/api/workflows").json()]
    assert wf_id not in ids

    r = client.post(f"/api/workflows/{wf_id}/run", json={"input": "hola"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "resource_inactive"
