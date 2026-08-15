"""Inventario unificado y grafo de relaciones del panel admin."""

from __future__ import annotations

import hashlib

from tests.api.admin._helpers import _AGENT_PAYLOAD

# ── Admin explore y grafo relacional ─────────────────────────────────────────


def test_admin_explore_unifies_and_filters_resource_types(admin_client):
    created = admin_client.post(
        "/api/agents", json={**_AGENT_PAYLOAD, "name": "Explore Agent Unique"}
    ).json()

    response = admin_client.get("/api/admin/explore?type=agent&q=unique")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == created["id"]
    assert payload["items"][0]["resource_type"] == "agent"
    assert set(payload["counts"]) == {
        "user",
        "group",
        "agent",
        "connection",
        "knowledge",
        "workflow",
        "llm_orchestration",
        "skill",
        "prompt",
        "tool",
        "memory",
    }


def test_admin_explore_rejects_unknown_type(admin_client):
    response = admin_client.get("/api/admin/explore?type=folder")

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "type"


def test_admin_explore_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "explorestandard",
            "email": "explorestandard@example.com",
            "password": "pass1234",
        },
    )

    assert client.get("/api/admin/explore").status_code == 403


def test_admin_agent_graph_contains_owner_connection_and_workflow(admin_client):
    import asyncio

    from app.storage.connection_storage import ConnectionStorage

    admin_user = next(
        user
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )
    connection = asyncio.run(
        ConnectionStorage().save(
            {
                "type": "openai",
                "label": "graph-connection",
                "api_key": "sk-test",
                "model": "gpt-4o",
            },
            owner_id=admin_user["id"],
        )
    )
    skill = admin_client.post(
        "/api/skills/private",
        json={
            "name": "Graph skill name",
            "description": "for graph test",
            "content": "do the thing",
        },
    ).json()
    admin_client.post("/api/memory/graph-memory-file", json={"content": "some notes"})
    memory_id = f"{admin_user['id']}::graph-memory-file"
    knowledge = admin_client.post(
        "/api/knowledge/text",
        json={"title": "Graph knowledge", "content": "Graph content"},
    ).json()
    agent = admin_client.post(
        "/api/agents",
        json={
            **_AGENT_PAYLOAD,
            "connection_id": connection["id"],
            "skills": [skill["id"]],
            "knowledge": [knowledge["id"]],
            "use_memory": True,
            "memory_file": "graph-memory-file",
        },
    ).json()
    workflow = admin_client.post(
        "/api/workflows",
        json={
            "name": "Graph workflow",
            "definition": {
                "nodes": [{"id": "step-one", "agent_id": agent["id"]}],
                "edges": [],
            },
        },
    )
    assert workflow.status_code in (200, 201)
    group = admin_client.post("/api/groups", json={"name": "Graph test group"}).json()

    response = admin_client.get(f"/api/admin/resources/agent/{agent['id']}/graph")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root_id"] == f"agent:{agent['id']}"
    node_types = {node["type"] for node in payload["nodes"]}
    assert {
        "agent",
        "user",
        "connection",
        "workflow",
        "skill",
        "memory",
        "knowledge",
    }.issubset(node_types)
    assert {edge["relation"] for edge in payload["edges"]} >= {
        "owns",
        "uses",
        "orchestrates",
    }
    skill_node = next(n for n in payload["nodes"] if n["type"] == "skill")
    assert skill_node["label"] == "Graph skill name"
    memory_node = next(n for n in payload["nodes"] if n["type"] == "memory")
    assert memory_node["label"] == "graph-memory-file"
    assert memory_node["id"] == f"memory:{memory_id}"

    for resource_type, resource_id in (
        ("user", admin_user["id"]),
        ("group", group["id"]),
        ("connection", connection["id"]),
        ("knowledge", knowledge["id"]),
        ("workflow", workflow.json()["id"]),
        ("skill", skill["id"]),
        ("memory", memory_id),
    ):
        related = admin_client.get(
            f"/api/admin/resources/{resource_type}/{resource_id}/graph"
        )
        assert related.status_code == 200
        assert related.json()["root_id"] == f"{resource_type}:{resource_id}"

    # El grafo del usuario propietario también debe incluir la skill y la
    # memoria — antes ninguno de los dos era un tipo conocido por Admin.
    user_graph = admin_client.get(
        f"/api/admin/resources/user/{admin_user['id']}/graph"
    ).json()
    user_node_types = {node["type"] for node in user_graph["nodes"]}
    assert "skill" in user_node_types
    assert "memory" in user_node_types

    # No basta con que los nodos aparezcan sueltos bajo el usuario: el grafo
    # debe dejar claro que es el AGENTE quien usa la skill/memoria/knowledge,
    # no solo que el usuario "posee" ambos por separado sin conectar.
    agent_node_id = f"agent:{agent['id']}"
    uses_targets = {
        edge["target_id"]
        for edge in user_graph["edges"]
        if edge["source_id"] == agent_node_id and edge["relation"] == "uses"
    }
    assert f"skill:{skill['id']}" in uses_targets
    assert f"knowledge:{knowledge['id']}" in uses_targets
    assert f"memory:{memory_id}" in uses_targets
    assert f"connection:{connection['id']}" in uses_targets


def test_admin_resource_graph_not_found(admin_client):
    response = admin_client.get("/api/admin/resources/agent/missing/graph")

    assert response.status_code == 404


def test_user_graph_nests_pack_files_under_the_pack(admin_client):
    admin_user = next(
        user
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )
    pack = admin_client.post(
        "/api/knowledge/packs",
        data={
            "name": "Scripts del grafo",
            "paths": '["ops/deploy.sh", "README.md"]',
        },
        files=[
            ("files", ("deploy.sh", b"echo deploy", "text/x-shellscript")),
            ("files", ("README.md", b"# Deploy", "text/markdown")),
        ],
    ).json()
    file_id = next(
        item["id"] for item in pack["items"] if item["relative_path"] == "ops/deploy.sh"
    )
    full_agent = admin_client.post(
        "/api/agents",
        json={
            **_AGENT_PAYLOAD,
            "name": "Agente con pack completo",
            "knowledge_packs": [pack["id"]],
        },
    ).json()
    partial_agent = admin_client.post(
        "/api/agents",
        json={
            **_AGENT_PAYLOAD,
            "name": "Agente con archivo concreto",
            "knowledge": [file_id],
        },
    ).json()

    graph = admin_client.get(
        f"/api/admin/resources/user/{admin_user['id']}/graph"
    ).json()
    edges = {
        (edge["source_id"], edge["target_id"], edge["relation"])
        for edge in graph["edges"]
    }
    user_id = f"user:{admin_user['id']}"
    pack_id = f"knowledge_pack:{pack['id']}"
    file_node_id = f"knowledge:{file_id}"
    directory_id = f"knowledge_directory:{pack['id']}:ops"

    assert (user_id, pack_id, "owns") in edges
    assert (pack_id, directory_id, "contains") in edges
    assert (directory_id, file_node_id, "contains") in edges
    assert not any(
        source == user_id and target == file_node_id for source, target, _ in edges
    )
    assert (f"agent:{full_agent['id']}", pack_id, "uses") in edges
    assert (f"agent:{partial_agent['id']}", pack_id, "uses_partial") in edges
    assert not any(
        source in {f"agent:{full_agent['id']}", f"agent:{partial_agent['id']}"}
        and target == file_node_id
        for source, target, _ in edges
    )

    stored = admin_client.get("/api/admin/knowledge").json()
    stored_file = next(item for item in stored if item["id"] == file_id)
    assert stored_file["checksum"] == hashlib.sha256(b"echo deploy").hexdigest()


def test_user_graph_groups_official_resources_under_repository(admin_client):
    import asyncio

    from app.storage.official_source_storage import OfficialSourceStorage

    admin_user = next(
        user
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )
    skill = admin_client.post(
        "/api/skills/private",
        json={"name": "Repository skill", "content": "# Repository skill"},
    ).json()

    async def mark() -> str:
        storage = OfficialSourceStorage()
        source = await storage.save_source(
            {
                "name": "Caveman",
                "repository_url": "https://github.com/juliusbrussee/caveman",
                "repository_owner": "juliusbrussee",
                "repository_name": "caveman",
                "repository_path": "juliusbrussee/caveman",
                "owner_id": admin_user["id"],
            }
        )
        await storage.mark_resource(
            "skill",
            skill["id"],
            admin_user["id"],
            source_id=source["id"],
            component_id="skills/repository-skill",
            source_path="skills/repository-skill/SKILL.md",
            content_hash="hash",
            commit_sha="abc123",
        )
        return str(source["id"])

    source_id = asyncio.run(mark())
    graph = admin_client.get(
        f"/api/admin/resources/user/{admin_user['id']}/graph"
    ).json()
    edges = {
        (edge["source_id"], edge["target_id"], edge["relation"])
        for edge in graph["edges"]
    }
    assert (f"user:{admin_user['id']}", f"official_source:{source_id}", "owns") in edges
    assert (f"official_source:{source_id}", f"skill:{skill['id']}", "origin") in edges
    assert (f"user:{admin_user['id']}", f"skill:{skill['id']}", "owns") not in edges


def test_user_graph_groups_synced_connections_under_provider_account(admin_client):
    import asyncio

    from app.storage.accounts import AccountStorage
    from app.storage.connection_storage import ConnectionStorage

    admin_user = next(
        user
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )

    async def seed() -> tuple[str, str]:
        account = await AccountStorage().save(
            {"provider": "openai", "name": "OpenAI Production", "api_key": "sk-test"},
            admin_user["id"],
        )
        connection = await ConnectionStorage().save(
            {
                "name": "OpenAI / gpt-4o",
                "type": "openai",
                "model": "gpt-4o",
                "api_key": "sk-test",
                "provider_account_id": account["id"],
            },
            admin_user["id"],
        )
        return str(account["id"]), str(connection["id"])

    account_id, connection_id = asyncio.run(seed())
    graph = admin_client.get(
        f"/api/admin/resources/user/{admin_user['id']}/graph"
    ).json()
    edges = {
        (edge["source_id"], edge["target_id"], edge["relation"])
        for edge in graph["edges"]
    }
    assert (f"user:{admin_user['id']}", f"provider:{account_id}", "owns") in edges
    assert (
        f"provider:{account_id}",
        f"connection:{connection_id}",
        "provides",
    ) in edges
    assert (
        f"user:{admin_user['id']}",
        f"connection:{connection_id}",
        "owns",
    ) not in edges
