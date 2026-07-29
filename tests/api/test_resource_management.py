def test_agent_save_creates_version_and_restores(admin_client):
    created = admin_client.post(
        "/api/agents",
        json={"name": "Versionado", "system_prompt": "Primera versión"},
    ).json()
    updated = {**created, "system_prompt": "Segunda versión"}
    assert admin_client.post("/api/agents", json=updated).status_code == 200

    history = admin_client.get(
        f"/api/resources/agent/{created['id']}/versions"
    )
    assert history.status_code == 200
    assert [item["version"] for item in history.json()] == [2, 1]

    restored = admin_client.post(
        f"/api/resources/agent/{created['id']}/versions/1/restore"
    )
    assert restored.status_code == 200
    assert restored.json()["system_prompt"] == "Primera versión"


def test_workflow_crud_validates_graph(admin_client):
    created = admin_client.post(
        "/api/agents",
        json={"name": "Paso", "system_prompt": "Procesa la entrada"},
    ).json()
    workflow = admin_client.post(
        "/api/workflows",
        json={
            "name": "Flujo lineal",
            "definition": {
                "nodes": [{"id": "one", "agent_id": created["id"]}],
                "edges": [],
            },
        },
    )
    assert workflow.status_code == 200
    workflow_id = workflow.json()["id"]
    assert admin_client.get("/api/workflows").json()[0]["id"] == workflow_id
    assert admin_client.delete(f"/api/workflows/{workflow_id}").json() == {"ok": True}


def test_workflow_run_exposes_sse_heartbeat_headers(admin_client, monkeypatch):
    created = admin_client.post(
        "/api/agents",
        json={"name": "Paso lento", "system_prompt": "Procesa la entrada"},
    ).json()
    workflow = admin_client.post(
        "/api/workflows",
        json={
            "name": "Flujo con latidos",
            "definition": {
                "nodes": [{"id": "one", "agent_id": created["id"]}],
                "edges": [],
            },
        },
    ).json()

    async def fake_run_workflow(_definition, _input, _resolve):
        yield {"type": "heartbeat"}
        yield {"type": "workflow_done", "output": "ok"}

    monkeypatch.setattr(
        "app.api.routes.resource_management.run_workflow", fake_run_workflow
    )

    response = admin_client.post(
        f"/api/workflows/{workflow['id']}/run",
        json={"input": "prueba"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.startswith(": keep-alive\n\n")
    assert '"type": "heartbeat"' not in response.text
    assert '"type": "workflow_done"' in response.text


def test_workflow_persists_canvas_positions_and_loops(admin_client):
    created = admin_client.post(
        "/api/agents",
        json={"name": "Agente cíclico", "system_prompt": "Procesa la entrada"},
    ).json()
    definition = {
        "nodes": [
            {
                "id": "draft",
                "agent_id": created["id"],
                "position": {"x": 120.25, "y": 80},
            },
            {
                "id": "review",
                "agent_id": created["id"],
                "position": {"x": 360, "y": 80},
            },
        ],
        "edges": [
            {"source": "draft", "target": "review", "type": "sequence"},
            {
                "source": "review",
                "target": "draft",
                "type": "loop",
                "mode": "fixed",
                "iterations": 3,
            },
        ],
    }

    response = admin_client.post(
        "/api/workflows",
        json={"name": "Flujo con ciclo", "definition": definition},
    )

    assert response.status_code == 200
    saved = response.json()["definition"]
    assert saved["nodes"][0]["position"] == {"x": 120.25, "y": 80.0}
    assert saved["edges"][1] == definition["edges"][1]

    listed = admin_client.get("/api/workflows").json()
    restored = next(item for item in listed if item["id"] == response.json()["id"])
    assert restored["definition"] == saved
