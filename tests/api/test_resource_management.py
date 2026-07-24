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
