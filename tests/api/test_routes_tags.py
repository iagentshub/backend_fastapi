"""Tests de soporte de etiquetas (tags) en agentes, skills y explore."""

from __future__ import annotations


def _login(client, username="tagstest", password="pass1234"):
    import asyncio

    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, password, email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def test_agent_tags_persisted_and_returned(client):
    """Guardar un agente con tags y verificar que aparecen en GET."""
    _login(client, "taguser1")
    r = client.post(
        "/api/agents",
        json={
            "name": "Tagged Agent",
            "description": "agente con etiquetas",
            "tags": ["python", "bot", "nlp"],
        },
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]

    r2 = client.get(f"/api/agents/{agent_id}")
    assert r2.status_code == 200
    data = r2.json()
    assert set(data.get("tags", [])) == {"python", "bot", "nlp"}


def test_agent_tags_go_to_resource_social(client):
    """Marcar un agente con tags como público y verificar que tags van a resource_social."""
    _login(client, "taguser2")
    r = client.post(
        "/api/agents",
        json={
            "name": "Public Tagged Agent",
            "description": "agente público con etiquetas",
            "tags": ["python", "automation"],
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]

    r2 = client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": True,
            "category": "Coding",
            "trial_missing_deps": "warn",
        },
    )
    assert r2.status_code == 200

    import asyncio
    import json as _json

    from app.storage.db import open_db

    async def _get_tags():
        async with open_db() as conn:
            return await conn.fetchone(
                "SELECT tags FROM resource_social WHERE resource_id = ?",
                (agent_id,),
            )

    row = asyncio.run(_get_tags())

    assert row is not None
    stored_tags = _json.loads(row["tags"])
    assert set(stored_tags) == {"python", "automation"}


def test_explore_filter_by_tag(client):
    """Filtrar explore por ?tag=python — solo devuelve recursos con esa tag."""
    _login(client, "taguser3")

    # Agente con tag python
    r = client.post(
        "/api/agents",
        json={
            "name": "Python Agent",
            "description": "agente python",
            "tags": ["python"],
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    python_id = r.json()["id"]
    client.put(
        f"/api/agents/private/{python_id}/visibility",
        json={
            "is_public": True,
            "category": "Coding",
            "trial_missing_deps": "warn",
        },
    )

    # Agente SIN tag python
    r2 = client.post(
        "/api/agents",
        json={
            "name": "Java Agent",
            "description": "agente java",
            "tags": ["java"],
            "labels": ["public"],
        },
    )
    assert r2.status_code == 200
    java_id = r2.json()["id"]
    client.put(
        f"/api/agents/private/{java_id}/visibility",
        json={
            "is_public": True,
            "category": "Coding",
            "trial_missing_deps": "warn",
        },
    )

    # Explore excluye los recursos propios del solicitante — se consulta como otro usuario
    _login(client, "taguser3_viewer")
    r3 = client.get("/api/explore", params={"tag": "python"})
    assert r3.status_code == 200
    results = r3.json()
    result_ids = [x["resource_id"] for x in results]
    assert python_id in result_ids
    assert java_id not in result_ids

    # Verificar que tags se deserializa como lista
    python_entry = next(x for x in results if x["resource_id"] == python_id)
    assert isinstance(python_entry["tags"], list)
    assert "python" in python_entry["tags"]


def test_skill_free_tags_rejected(client):
    """Las skills solo usan el catálogo de labels; no admiten tags libres."""
    _login(client, "taguser4")
    r = client.post(
        "/api/skills/private",
        json={
            "name": "Tagged Skill",
            "description": "skill con etiquetas",
            "content": "# instrucciones de la skill",
            "tags": ["nlp", "text", "ml"],
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "tags"


def test_agent_legacy_language_is_mirrored_to_canonical_label(client):
    _login(client, "taglanguagelegacy")
    response = client.post(
        "/api/agents",
        json={
            "name": "Agente heredado",
            "language": "es",
            "labels": ["private"],
        },
    )
    assert response.status_code == 200
    assert response.json()["language"] == "es"
    assert "lang_es" in response.json()["labels"]


def test_explore_language_filter_is_anded_with_regular_labels(client):
    _login(client, "taglanguageowner")

    def _publish(name, labels):
        created = client.post(
            "/api/agents",
            json={"name": name, "description": name, "labels": labels},
        )
        assert created.status_code == 200
        agent_id = created.json()["id"]
        visible = client.put(
            f"/api/agents/private/{agent_id}/visibility",
            json={
                "is_public": True,
                "category": "Writing",
                "trial_missing_deps": "warn",
            },
        )
        assert visible.status_code == 200
        return agent_id

    spanish_production = _publish(
        "Spanish production", ["public", "production", "lang_es"]
    )
    english_production = _publish(
        "English production", ["public", "production", "lang_en"]
    )
    spanish_draft = _publish("Spanish draft", ["public", "draft", "lang_es"])

    _login(client, "taglanguageviewer")
    response = client.get(
        "/api/explore", params={"language": "es", "label": "production"}
    )
    assert response.status_code == 200
    result_ids = {item["resource_id"] for item in response.json()}
    assert spanish_production in result_ids
    assert english_production not in result_ids
    assert spanish_draft not in result_ids
    item = next(
        item for item in response.json() if item["resource_id"] == spanish_production
    )
    assert item["languages"] == ["es"]


def test_explore_rejects_unknown_content_language(client):
    _login(client, "taglanguageinvalid")
    response = client.get("/api/explore", params={"language": "klingon"})
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "language"


def test_language_labels_are_valid_for_skill_and_knowledge(client):
    _login(client, "taglanguageresources")
    skill = client.post(
        "/api/skills/private",
        json={
            "name": "Redacción española",
            "description": "Instrucciones en español",
            "content": "# Instrucciones\n\nEscribe siempre en español.",
            "labels": ["private", "lang_es"],
        },
    )
    assert skill.status_code == 200
    assert "lang_es" in skill.json()["labels"]

    knowledge = client.post(
        "/api/knowledge/text",
        json={
            "title": "Manual español",
            "content": "Contenido de referencia en español.",
            "labels": ["private", "lang_es"],
        },
    )
    assert knowledge.status_code == 200
    assert knowledge.json()["labels"] == ["private", "lang_es"]

    listed = client.get("/api/knowledge")
    assert listed.status_code == 200
    stored = next(item for item in listed.json() if item["id"] == knowledge.json()["id"])
    assert stored["labels"] == ["private", "lang_es"]

    workflow_agent = client.post(
        "/api/agents", json={"name": "Paso del flujo"}
    ).json()
    workflow = client.post(
        "/api/workflows",
        json={
            "name": "Flujo bilingüe",
            "description": "Flujo en español e inglés",
            "definition": {
                "nodes": [{"id": "one", "agent_id": workflow_agent["id"]}],
                "edges": [],
            },
            "labels": ["private", "lang_es", "lang_en"],
        },
    )
    assert workflow.status_code == 200
    assert workflow.json()["labels"] == ["private", "lang_es", "lang_en"]
