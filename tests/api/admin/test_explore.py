"""Inventario unificado y grafo de relaciones del panel admin."""

from __future__ import annotations

import hashlib

from tests.api.admin._helpers import _AGENT_PAYLOAD

# ── Admin explore y grafo relacional ─────────────────────────────────────────


def test_admin_explore_unifies_and_filters_resource_types(admin_client):
    created = admin_client.post(
        "/api/agents", json={**_AGENT_PAYLOAD, "name": "Explore Agent Unique"}
    ).json()

    response = admin_client.get(
        "/api/v2/admin/explore?type=agent&q=unique&include_total=true"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"]["total"] == 1
    assert payload["items"][0]["id"] == created["id"]
    assert payload["items"][0]["resource_type"] == "agent"
    assert payload["counts"] is None

    counted = admin_client.get(
        "/api/v2/admin/explore?type=agent&q=unique&include_counts=true"
    ).json()
    assert set(counted["counts"]) == {
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
    response = admin_client.get("/api/v2/admin/explore?type=folder")

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

    assert client.get("/api/v2/admin/explore").status_code == 403


def _via(item):
    """El (tipo, id) del que cuelga una relación, o None si es la raíz."""
    via = item.get("via")
    return (via["type"], via["id"]) if via else None


def test_admin_agent_graph_contains_owner_connection_and_workflow(admin_client):
    import asyncio

    from app.storage.connection_storage import ConnectionStorage

    admin_user = next(
        user
        for user in admin_client.get("/api/v2/admin/explore?type=user&limit=100").json()["items"]
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

    response = admin_client.get(f"/api/admin/resources/agent/{agent['id']}/relations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root"] == {
        "type": "agent",
        "id": agent["id"],
        "label": payload["root"]["label"],
        "description": payload["root"]["description"],
    }
    tipos = {item["type"] for item in payload["items"]}
    assert {
        "user",
        "connection",
        "workflow",
        "skill",
        "memory",
        "knowledge",
    }.issubset(tipos)
    assert {item["relation"] for item in payload["items"]} >= {
        "owns",
        "uses",
        "orchestrates",
    }
    skill_item = next(i for i in payload["items"] if i["type"] == "skill")
    assert skill_item["label"] == "Graph skill name"
    memory_item = next(i for i in payload["items"] if i["type"] == "memory")
    assert memory_item["label"] == "graph-memory-file"
    assert memory_item["id"] == memory_id

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
            f"/api/admin/resources/{resource_type}/{resource_id}/relations"
        )
        assert related.status_code == 200
        raiz = related.json()["root"]
        assert (raiz["type"], raiz["id"]) == (resource_type, resource_id)

    # El grafo del usuario propietario también debe incluir la skill y la
    # memoria — antes ninguno de los dos era un tipo conocido por Admin.
    user_graph = admin_client.get(
        f"/api/admin/resources/user/{admin_user['id']}/relations"
    ).json()
    tipos_del_usuario = {item["type"] for item in user_graph["items"]}
    assert "skill" in tipos_del_usuario
    assert "memory" in tipos_del_usuario

    # No basta con que los recursos aparezcan sueltos bajo el usuario: tiene
    # que constar que es el AGENTE quien usa la skill/memoria/knowledge, no
    # solo que el usuario "posee" ambos por separado sin conectar.
    usados_por_el_agente = {
        (item["type"], item["id"])
        for item in user_graph["items"]
        if item["relation"] == "uses"
        and item["via"] == {"type": "agent", "id": agent["id"]}
    }
    assert ("skill", skill["id"]) in usados_por_el_agente
    assert ("knowledge", knowledge["id"]) in usados_por_el_agente
    assert ("memory", memory_id) in usados_por_el_agente
    assert ("connection", connection["id"]) in usados_por_el_agente


def test_admin_resource_graph_not_found(admin_client):
    response = admin_client.get("/api/admin/resources/agent/missing/relations")

    assert response.status_code == 404


def test_user_graph_nests_pack_files_under_the_pack(admin_client):
    admin_user = next(
        user
        for user in admin_client.get("/api/v2/admin/explore?type=user&limit=100").json()["items"]
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
        f"/api/admin/resources/user/{admin_user['id']}/relations"
    ).json()
    hechos = {
        (item["type"], item["id"], item["relation"], _via(item))
        for item in graph["items"]
    }
    pack_via = ("knowledge_pack", pack["id"])

    # El pack cuelga del usuario y el fichero del pack, con su ruta: las
    # carpetas las construye el cliente a partir de `path`.
    assert ("knowledge_pack", pack["id"], "owns", None) in hechos
    assert ("knowledge", file_id, "contains", pack_via) in hechos
    fichero = next(
        item
        for item in graph["items"]
        if item["type"] == "knowledge" and item["id"] == file_id
    )
    assert fichero["path"] == "ops/deploy.sh"
    # Y nunca cuelga directamente del usuario, que es lo que lo dejaba plano.
    assert ("knowledge", file_id, "owns", None) not in hechos

    assert (
        "knowledge_pack",
        pack["id"],
        "uses",
        ("agent", full_agent["id"]),
    ) in hechos
    assert (
        "knowledge_pack",
        pack["id"],
        "uses_partial",
        ("agent", partial_agent["id"]),
    ) in hechos
    # Ningún agente enlaza el fichero suelto: siempre a través de su pack.
    assert not any(
        item["type"] == "knowledge"
        and item["id"] == file_id
        and _via(item) in {("agent", full_agent["id"]), ("agent", partial_agent["id"])}
        for item in graph["items"]
    )

    stored = admin_client.get("/api/v2/admin/explore?type=knowledge&limit=100").json()["items"]
    stored_file = next(item for item in stored if item["id"] == file_id)
    assert stored_file["checksum"] == hashlib.sha256(b"echo deploy").hexdigest()


def test_user_graph_groups_official_resources_under_repository(admin_client):
    import asyncio

    from app.storage.official_source_storage import OfficialSourceStorage

    admin_user = next(
        user
        for user in admin_client.get("/api/v2/admin/explore?type=user&limit=100").json()["items"]
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
        f"/api/admin/resources/user/{admin_user['id']}/relations"
    ).json()
    hechos = {
        (item["type"], item["id"], item["relation"], _via(item))
        for item in graph["items"]
    }
    # La skill cuelga del repositorio del que vino, no del usuario: así se ve
    # de dónde salió y no solo quién la tiene.
    assert ("official_source", source_id, "owns", None) in hechos
    assert (
        "skill",
        skill["id"],
        "origin",
        ("official_source", source_id),
    ) in hechos
    assert ("skill", skill["id"], "owns", None) not in hechos


def test_user_graph_groups_synced_connections_under_provider_account(admin_client):
    import asyncio

    from app.storage.accounts import AccountStorage
    from app.storage.connection_storage import ConnectionStorage

    admin_user = next(
        user
        for user in admin_client.get("/api/v2/admin/explore?type=user&limit=100").json()["items"]
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
        f"/api/admin/resources/user/{admin_user['id']}/relations"
    ).json()
    hechos = {
        (item["type"], item["id"], item["relation"], _via(item))
        for item in graph["items"]
    }
    assert ("provider", account_id, "owns", None) in hechos
    assert (
        "connection",
        connection_id,
        "provides",
        ("provider", account_id),
    ) in hechos
    assert ("connection", connection_id, "owns", None) not in hechos
