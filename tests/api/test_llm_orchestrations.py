from __future__ import annotations

import asyncio

from app.auth.auth import create_token, register_user


def _connection(client, name: str) -> dict:
    return client.post(
        "/api/connections",
        json={
            "type": "openai",
            "label": name,
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
        },
    ).json()


def _register(username: str) -> None:
    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass


def _as(client, username: str, group_id: str | None = None) -> None:
    client.cookies.set("ga_token", create_token(username, group_id=group_id))


def test_llm_orchestration_crud_is_private_and_validated(admin_client):
    first = _connection(admin_client, "First")
    second = _connection(admin_client, "Second")
    response = admin_client.post(
        "/api/llm-orchestrations",
        json={
            "name": "Fallback privado",
            "mode": "stack",
            "candidates": [
                {"connection_id": first["id"], "routing_hint": "rápida"},
                {"connection_id": second["id"], "routing_hint": "reserva"},
            ],
        },
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved["scope"] == "private"
    assert saved["resource_type"] == "llm_orchestration"
    assert admin_client.get("/api/llm-orchestrations").json()[0]["id"] == saved["id"]

    invalid = admin_client.post(
        "/api/llm-orchestrations",
        json={
            "name": "Duplicada",
            "mode": "stack",
            "candidates": [
                {"connection_id": first["id"]},
                {"connection_id": first["id"]},
            ],
        },
    )
    assert invalid.status_code == 422


def test_balanced_requires_router_and_is_exposed_as_a_connection(admin_client):
    first = _connection(admin_client, "One")
    second = _connection(admin_client, "Two")
    missing_router = admin_client.post(
        "/api/llm-orchestrations",
        json={
            "name": "Balanced",
            "mode": "balanced",
            "candidates": [
                {"connection_id": first["id"]},
                {"connection_id": second["id"]},
            ],
        },
    )
    assert missing_router.status_code == 422

    orchestration = admin_client.post(
        "/api/llm-orchestrations",
        json={
            "name": "Balanced",
            "mode": "balanced",
            "router_connection_id": first["id"],
            "candidates": [
                {"connection_id": first["id"]},
                {"connection_id": second["id"]},
            ],
        },
    ).json()
    virtual_id = f"llm-orchestration:{orchestration['id']}"
    connections = admin_client.get("/api/v2/connections").json()["items"]
    virtual = next(item for item in connections if item["id"] == virtual_id)
    assert virtual["type"] == "llm_orchestration"
    assert virtual["model"] == "balanced"
    assert virtual["is_virtual"] is True
    agent = admin_client.post(
        "/api/agents",
        json={
            "name": "Routed",
            "system_prompt": "Ayuda",
            "connection_id": virtual_id,
        },
    )
    assert agent.status_code == 200
    assert agent.json()["connection_id"] == virtual_id
    assert "llm_orchestration_id" not in agent.json()
    assert (
        admin_client.delete(
            f"/api/llm-orchestrations/{orchestration['id']}"
        ).status_code
        == 409
    )


def test_legacy_connection_preference_remains_compatible(admin_client):
    first = _connection(admin_client, "Preference one")
    second = _connection(admin_client, "Preference two")
    agent = admin_client.post(
        "/api/agents",
        json={"name": "Legacy preference", "connection_id": first["id"]},
    ).json()

    legacy = admin_client.put(
        f"/api/agents/{agent['id']}/preferences",
        json={"connection_id": second["id"]},
    )
    assert legacy.status_code == 200
    saved_legacy = admin_client.get(f"/api/agents/{agent['id']}/preferences").json()
    assert saved_legacy == {
        "connection_id": second["id"],
    }

    orchestration = admin_client.post(
        "/api/llm-orchestrations",
        json={
            "name": "Preference route",
            "mode": "stack",
            "candidates": [
                {"connection_id": first["id"]},
                {"connection_id": second["id"]},
            ],
        },
    ).json()
    routed = admin_client.put(
        f"/api/agents/{agent['id']}/preferences",
        json={"connection_id": f"llm-orchestration:{orchestration['id']}"},
    )
    assert routed.status_code == 200
    saved_routed = admin_client.get(f"/api/agents/{agent['id']}/preferences").json()
    assert saved_routed == {
        "connection_id": f"llm-orchestration:{orchestration['id']}",
    }
    assert (
        admin_client.delete(
            f"/api/llm-orchestrations/{orchestration['id']}"
        ).status_code
        == 409
    )


def test_llm_orchestration_appears_only_in_admin_explore(admin_client):
    first = _connection(admin_client, "Admin one")
    second = _connection(admin_client, "Admin two")
    item = admin_client.post(
        "/api/llm-orchestrations",
        json={
            "name": "Admin card",
            "mode": "stack",
            "candidates": [
                {"connection_id": first["id"]},
                {"connection_id": second["id"]},
            ],
        },
    ).json()
    admin_items = admin_client.get("/api/v2/admin/explore?type=llm_orchestration").json()[
        "items"
    ]
    assert [entry["id"] for entry in admin_items] == [item["id"]]
    public_items = admin_client.get("/api/v2/explore").json()["items"]
    assert all(
        entry.get("resource_type") != "llm_orchestration" for entry in public_items
    )
    assert item["id"] not in {
        entry.get("id") or entry.get("resource_id") for entry in public_items
    }


def test_llm_orchestration_can_be_shared_privately_with_a_group(admin_client):
    first = _connection(admin_client, "Shared first")
    second = _connection(admin_client, "Shared second")
    item = admin_client.post(
        "/api/llm-orchestrations",
        json={
            "name": "Group-only route",
            "mode": "stack",
            "candidates": [
                {"connection_id": first["id"]},
                {"connection_id": second["id"]},
            ],
        },
    ).json()
    group = admin_client.post(
        "/api/groups", json={"name": "Private routing group"}
    ).json()

    shared = admin_client.post(
        f"/api/sharing/llm_orchestration/{item['id']}",
        json={"group_id": group["id"]},
    )
    assert shared.status_code == 200
    assert shared.json()["cascaded"] == []

    route_groups = admin_client.get(
        f"/api/sharing/llm_orchestration/{item['id']}/groups"
    ).json()["group_ids"]
    assert group["id"] in route_groups
    for connection in (first, second):
        connection_groups = admin_client.get(
            f"/api/sharing/connection/{connection['id']}/groups"
        ).json()["group_ids"]
        assert group["id"] not in connection_groups

    public_items = admin_client.get("/api/v2/explore").json()["items"]
    assert all(
        entry.get("resource_type") != "llm_orchestration" for entry in public_items
    )


def test_group_member_can_use_shared_orchestration_without_publication(
    client, monkeypatch
):
    _register("llm_route_owner")
    _register("llm_route_member")
    _as(client, "llm_route_owner")
    first = _connection(client, "Member first")
    second = _connection(client, "Member second")
    item = client.post(
        "/api/llm-orchestrations",
        json={
            "name": "Shared team route",
            "mode": "stack",
            "candidates": [
                {"connection_id": first["id"]},
                {"connection_id": second["id"]},
            ],
        },
    ).json()
    group = client.post("/api/groups", json={"name": "LLM routing team"}).json()
    assert (
        client.post(
            f"/api/groups/{group['id']}/members",
            json={"username": "llm_route_member", "role": "member"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/sharing/llm_orchestration/{item['id']}",
            json={"group_id": group["id"]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/sharing/connection/{second['id']}",
            json={"group_id": group["id"]},
        ).status_code
        == 200
    )

    _as(client, "llm_route_member", group["id"])
    routes = client.get("/api/llm-orchestrations").json()
    shared = next(route for route in routes if route["id"] == item["id"])
    assert shared["_shared"] is True
    assert shared["_binding_configured"] is False
    assert {candidate["connection_id"] for candidate in shared["candidates"]} == {""}
    available_connection_ids = {
        connection["id"] for connection in client.get("/api/v2/connections").json()["items"]
    }
    assert first["id"] not in available_connection_ids
    assert second["id"] in available_connection_ids
    virtual_id = f"llm-orchestration:{item['id']}"
    assert virtual_id not in available_connection_ids

    member_first = _connection(client, "Member own first")
    binding = client.put(
        f"/api/llm-orchestrations/{item['id']}/binding",
        json={
            "candidates": [
                {"connection_id": member_first["id"], "routing_hint": "rápida"},
                {"connection_id": second["id"], "routing_hint": "reserva"},
            ]
        },
    )
    assert binding.status_code == 200
    assert binding.json()["_binding_configured"] is True
    assert [
        candidate["routing_hint"] for candidate in binding.json()["candidates"]
    ] == ["", ""]
    available_connection_ids = {
        connection["id"] for connection in client.get("/api/v2/connections").json()["items"]
    }
    assert virtual_id in available_connection_ids

    agent = client.post(
        "/api/agents",
        json={"name": "Member routed", "connection_id": member_first["id"]},
    )
    assert agent.status_code == 200
    assert agent.json()["connection_id"] == member_first["id"]
    workflow = client.post(
        "/api/workflows",
        json={
            "name": "Member routed workflow",
            "definition": {
                "nodes": [{"id": "one", "agent_id": agent.json()["id"]}],
                "edges": [],
                "llm_orchestration_connection_id": virtual_id,
            },
        },
    )
    assert workflow.status_code == 200
    assert (
        workflow.json()["definition"]["llm_orchestration_connection_id"] == virtual_id
    )

    async def inspect_run(_definition, _input, resolve, *, consume_quota=None):
        _, resolved = await resolve(agent.json()["id"])
        assert resolved["id"] == virtual_id
        assert set(resolved["_connections"]) == {member_first["id"], second["id"]}
        yield {"type": "workflow_done", "output": "ok"}

    monkeypatch.setattr("app.api.routes.resource_management.run_workflow", inspect_run)
    run = client.post(
        f"/api/workflows/{workflow.json()['id']}/run", json={"input": "hola"}
    )
    assert run.status_code == 200
    assert '"type": "workflow_done"' in run.text
    assert (
        client.post(f"/api/connections/{member_first['id']}/deactivate").status_code
        == 200
    )
    available_connection_ids = {
        connection["id"] for connection in client.get("/api/v2/connections").json()["items"]
    }
    assert virtual_id not in available_connection_ids
    assert all(
        entry.get("resource_type") != "llm_orchestration"
        for entry in client.get("/api/v2/explore").json()["items"]
    )
