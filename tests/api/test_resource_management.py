import time


def test_agent_save_creates_version_and_restores(admin_client):
    created = admin_client.post(
        "/api/agents",
        json={"name": "Versionado", "system_prompt": "Primera versión"},
    ).json()
    updated = {**created, "system_prompt": "Segunda versión"}
    assert admin_client.post("/api/agents", json=updated).status_code == 200

    history = admin_client.get(f"/api/resources/agent/{created['id']}/versions")
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


def test_workflow_sse_hides_unexpected_exception_details(admin_client, monkeypatch):
    workflow = _workflow_for_run(admin_client, "Flujo con fallo interno")

    async def failing_workflow(_definition, _input, _resolve):
        if False:
            yield {}
        raise RuntimeError("secreto SQL /srv/private.db")

    monkeypatch.setattr(
        "app.api.routes.resource_management.run_workflow", failing_workflow
    )
    response = admin_client.post(
        f"/api/workflows/{workflow['id']}/run", json={"input": "prueba"}
    )
    assert response.status_code == 200
    assert '"code": "internal_error"' in response.text
    assert "secreto SQL" not in response.text


def test_persisted_workflow_run_replays_after_start_response(admin_client, monkeypatch):
    workflow = _workflow_for_run(admin_client)

    async def fake_run_workflow(_definition, _input, _resolve):
        yield {"type": "stage_started", "node_id": "one"}
        yield {"type": "stage_done", "node_id": "one", "output": "resultado"}
        yield {"type": "workflow_done", "output": "resultado"}

    monkeypatch.setattr(
        "app.services.workflow_run_executor.run_workflow", fake_run_workflow
    )
    started = admin_client.post(
        f"/api/workflows/{workflow['id']}/runs", json={"input": "prueba"}
    )
    assert started.status_code == 202
    run_id = started.json()["id"]

    completed = _wait_run(admin_client, run_id, "completed")
    assert completed["final_output"] == "resultado"
    assert completed["definition"] == workflow["definition"]
    assert completed["progress"]["completed"] == 1

    replay = admin_client.get(f"/api/workflow-runs/{run_id}/events?after=1")
    assert replay.status_code == 200
    assert '"sequence": 1' not in replay.text
    assert '"sequence": 2' in replay.text
    assert '"type": "workflow_done"' in replay.text


def test_persisted_workflow_run_is_cancelled_on_server(admin_client, monkeypatch):
    import asyncio

    workflow = _workflow_for_run(admin_client, "Flujo cancelable")

    async def slow_run_workflow(_definition, _input, _resolve):
        yield {"type": "stage_started", "node_id": "one"}
        while True:
            await asyncio.sleep(0.02)
            yield {"type": "heartbeat"}

    monkeypatch.setattr(
        "app.services.workflow_run_executor.run_workflow", slow_run_workflow
    )
    started = admin_client.post(
        f"/api/workflows/{workflow['id']}/runs", json={"input": "prueba"}
    ).json()
    _wait_run(admin_client, started["id"], "running")

    cancelling = admin_client.post(f"/api/workflow-runs/{started['id']}/cancel")
    assert cancelling.status_code == 200
    assert cancelling.json()["status"] in {"cancelling", "cancelled"}
    cancelled = _wait_run(admin_client, started["id"], "cancelled")
    assert cancelled["finished_at"] is not None


def test_missing_workflow_run_endpoints_return_structured_404(admin_client):
    for method, path in (
        ("get", "/api/workflow-runs/missing"),
        ("get", "/api/workflow-runs/missing/events"),
        ("post", "/api/workflow-runs/missing/cancel"),
    ):
        response = getattr(admin_client, method)(path)
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert detail["code"] == "not_found"
        assert detail["resource"] == "workflow_run"


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


def _workflow_for_run(client, name="Flujo persistente"):
    agent = client.post(
        "/api/agents",
        json={"name": "Paso persistente", "system_prompt": "Procesa"},
    ).json()
    return client.post(
        "/api/workflows",
        json={
            "name": name,
            "definition": {
                "nodes": [{"id": "one", "agent_id": agent["id"]}],
                "edges": [],
            },
        },
    ).json()


def _wait_run(client, run_id, expected, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/workflow-runs/{run_id}")
        if response.status_code == 200 and response.json()["status"] == expected:
            return response.json()
        time.sleep(0.03)
    raise AssertionError(f"La ejecución {run_id} no alcanzó {expected}")
