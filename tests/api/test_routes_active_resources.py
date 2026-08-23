"""Borrado suave en recursos operativos: conexiones y workflows."""

from __future__ import annotations

import asyncio

import pytest


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


@pytest.mark.parametrize(
    ("create_path", "payload", "deactivate_path", "list_path"),
    [
        (
            "/api/skills/private",
            {"name": "Skill activable", "category": "ai", "content": "secreto"},
            "/api/skills/private/{id}/deactivate",
            "/api/skills",
        ),
        (
            "/api/prompts/private",
            {"name": "Prompt activable", "alias": "activar_test", "content": "secreto"},
            "/api/prompts/private/{id}/deactivate",
            "/api/prompts",
        ),
        (
            "/api/tools/private",
            {"name": "Tool activable", "language": "python", "content": "secreto"},
            "/api/tools/private/{id}/deactivate",
            "/api/tools",
        ),
        (
            "/api/knowledge/text",
            {"title": "Documento activable", "content": "secreto"},
            "/api/knowledge/{id}/deactivate",
            "/api/knowledge",
        ),
    ],
)
def test_content_deactivate_keeps_card_visible_and_reactivates(
    client, create_path, payload, deactivate_path, list_path
):
    resource_name = str(payload.get("name") or payload.get("title") or "content")
    _login(client, f"content_{resource_name.split()[0].lower()}")
    created_response = client.post(create_path, json=payload)
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()

    response = client.post(deactivate_path.format(id=created["id"]))
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False

    listed = client.get(list_path).json()
    inactive = next(item for item in listed if item["id"] == created["id"])
    assert inactive["is_active"] is False

    activate_path = deactivate_path.replace("/deactivate", "/activate")
    response = client.post(activate_path.format(id=created["id"]))
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True


def test_deactivated_public_content_disappears_from_explore(client):
    from app.auth.auth import create_token

    _login(client, "content_explore")
    skill = client.post(
        "/api/skills/public",
        json={
            "name": "Skill pública activable",
            "category": "ai",
            "content": "contenido",
            "labels": ["public"],
        },
    ).json()
    published = client.put(
        f"/api/skills/public/{skill['id']}/visibility",
        json={"is_public": True, "category": "Coding"},
    )
    assert published.status_code == 200, published.text
    _login(client, "content_viewer")
    assert skill["id"] in {
        item["resource_id"] for item in client.get("/api/explore").json()
    }

    client.cookies.set("ga_token", create_token("content_explore"))
    response = client.post(f"/api/skills/public/{skill['id']}/deactivate")
    assert response.status_code == 200, response.text
    client.cookies.set("ga_token", create_token("content_viewer"))
    assert skill["id"] not in {
        item["resource_id"] for item in client.get("/api/explore").json()
    }
    assert client.get(f"/api/explore/skill/{skill['id']}/preview").status_code == 404


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


def test_deactivated_agent_blocks_workflow_run(client):
    _login(client, "actres_wf_agent")
    agent = client.post("/api/agents", json={"name": "Paso desactivado"}).json()
    workflow = client.post(
        "/api/workflows",
        json={
            "name": "Flujo con agente desactivado",
            "definition": _minimal_workflow_definition(agent["id"]),
        },
    ).json()
    client.post(f"/api/agents/{agent['id']}/deactivate")

    response = client.post(
        f"/api/workflows/{workflow['id']}/run", json={"input": "hola"}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "resource_inactive"
    assert response.json()["detail"]["resource"] == "agent"


def test_deactivated_connection_blocks_workflow_run(client):
    _login(client, "actres_wf_connection")
    connection = client.post(
        "/api/connections",
        json={"name": "Conn workflow", "type": "openai", "api_key": "sk-test"},
    ).json()
    agent = client.post(
        "/api/agents",
        json={"name": "Paso conectado", "connection_id": connection["id"]},
    ).json()
    workflow = client.post(
        "/api/workflows",
        json={
            "name": "Flujo con conexión desactivada",
            "definition": _minimal_workflow_definition(agent["id"]),
        },
    ).json()
    client.post(f"/api/connections/{connection['id']}/deactivate")

    response = client.post(
        f"/api/workflows/{workflow['id']}/run", json={"input": "hola"}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "resource_inactive"
    assert response.json()["detail"]["resource"] == "connection"
