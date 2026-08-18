"""Tests de la Fase 2 social: visibilidad, explore, stars y recursos de usuario."""

from __future__ import annotations


def _login(client, username="socialtest", password="pass1234"):
    import asyncio

    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, password, email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _switch(client, username):
    """Cambia la sesión a un usuario YA registrado, sin volver a crearlo."""
    from app.auth.auth import create_token

    client.cookies.set("ga_token", create_token(username))
    return username


def _create_agent(client, name="Social Agent"):
    r = client.post(
        "/api/agents",
        json={
            "name": name,
            "description": "agente de prueba social",
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    return r.json()


def _create_skill(client, name="Social Skill"):
    r = client.post(
        "/api/skills/private",
        json={
            "name": name,
            "description": "skill de prueba social",
            "content": "# instrucciones",
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    return r.json()


def test_agente_publico_aparece_en_explore(client):
    user = _login(client, "exploretest1")
    agent = _create_agent(client, "Explore Agent One")
    agent_id = agent["id"]

    r = client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": True,
            "category": "Coding",
            "trial_missing_deps": "warn",
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Explore excluye los recursos propios del solicitante — se consulta como otro usuario
    _login(client, "exploretest1_viewer")
    r = client.get("/api/explore")
    assert r.status_code == 200
    ids = [x["resource_id"] for x in r.json()]
    assert agent_id in ids

    found = next(x for x in r.json() if x["resource_id"] == agent_id)
    assert found["resource_type"] == "agent"
    assert found["owner"] != user
    assert found["owner_username"] == user
    assert found["category"] == "Coding"


def test_crear_agente_publico_lo_publica_sin_segunda_peticion(client):
    owner = _login(client, "explore_direct_owner")
    response = client.post(
        "/api/agents",
        json={
            "name": "Agente público directo",
            "description": "Debe aparecer al guardarlo",
            "scope": "public",
            "labels": ["public"],
        },
    )
    assert response.status_code == 200, response.text
    agent_id = response.json()["id"]

    _login(client, "explore_direct_viewer")
    response = client.get(
        "/api/explore", params={"type": "agent", "q": "Agente público directo"}
    )

    assert response.status_code == 200
    assert [item["resource_id"] for item in response.json()] == [agent_id]
    assert response.json()[0]["owner"] != owner
    assert response.json()[0]["owner_username"] == owner


def test_hacer_privado_un_agente_guardado_lo_retira_de_explore(client):
    _login(client, "explore_direct_private_owner")
    created = client.post(
        "/api/agents",
        json={
            "name": "Agente que vuelve a privado",
            "scope": "public",
            "labels": ["public"],
        },
    ).json()

    response = client.post(
        "/api/agents",
        json={
            "id": created["id"],
            "name": created["name"],
            "scope": "private",
            "labels": ["private"],
        },
    )
    assert response.status_code == 200, response.text

    _login(client, "explore_direct_private_viewer")
    response = client.get(
        "/api/explore", params={"type": "agent", "q": created["name"]}
    )
    assert response.status_code == 200
    assert response.json() == []


def test_grafo_publico_de_agente_incluye_solo_dependencias_publicadas(client):
    _login(client, "explore_graph_owner")
    public_skill = _create_skill(client, "Skill pública del grafo")
    client.put(
        f"/api/skills/private/{public_skill['id']}/visibility",
        json={"is_public": True, "category": "Coding"},
    )
    private_skill = client.post(
        "/api/skills/private",
        json={"name": "Skill privada del grafo", "content": "# privada"},
    ).json()
    agent = client.post(
        "/api/agents",
        json={
            "name": "Agente con grafo público",
            "scope": "public",
            "labels": ["public"],
            "skills": [public_skill["id"], private_skill["id"]],
            "publish_dependencies": [f"skill:{public_skill['id']}"],
        },
    ).json()

    # Publicar el agente publica sus dependencias propias en cascada. Simula
    # que una de ellas se retiró después del catálogo: el grafo no debe
    # revelar su nombre solo porque el agente aún conserve el ID.
    import asyncio

    from app.storage.db import open_db

    async def unpublish_private_dependency() -> None:
        async with open_db() as conn:
            await conn.execute(
                "DELETE FROM resource_social WHERE resource_type=? AND resource_id=?",
                ("skill", private_skill["id"]),
            )
            await conn.commit()

    asyncio.run(unpublish_private_dependency())

    _login(client, "explore_graph_viewer")
    response = client.get(f"/api/explore/agent/{agent['id']}/relations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root"]["id"] == agent["id"]
    referidos = {(item["type"], item["id"]) for item in payload["items"]}
    assert ("skill", public_skill["id"]) in referidos
    assert ("skill", private_skill["id"]) not in referidos


def test_publicar_agente_materializa_solo_dependencias_elegidas(client):
    import asyncio

    from app.storage.db import open_db

    _login(client, "publish_selection_owner")
    skill = _create_skill(client, "Skill elegida")
    knowledge = client.post(
        "/api/knowledge/text",
        json={"title": "Documento no elegido", "content": "privado"},
    ).json()

    response = client.post(
        "/api/agents",
        json={
            "name": "Agente con selección explícita",
            "scope": "public",
            "labels": ["public"],
            "skills": [skill["id"]],
            "knowledge": [knowledge["id"]],
            "publish_dependencies": [f"skill:{skill['id']}"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["public_dependencies"] == [f"skill:{skill['id']}"]

    async def published_types() -> set[tuple[str, str]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT resource_type, resource_id FROM resource_social WHERE is_public=1"
            )
            return {(row["resource_type"], row["resource_id"]) for row in rows}

    published = asyncio.run(published_types())
    assert ("skill", skill["id"]) in published
    assert ("knowledge", knowledge["id"]) not in published


def test_publicar_agente_rechaza_dependencias_ajenas_y_conexiones(client):
    _login(client, "publish_selection_invalid")
    response = client.post(
        "/api/agents",
        json={
            "name": "Agente con selección inválida",
            "scope": "public",
            "labels": ["public"],
            "publish_dependencies": ["connection:private-connection"],
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_field"
    assert detail["field"] == "publish_dependencies"


def test_grafo_publico_de_workflow_conserva_flujo_y_recursos(client):
    _login(client, "explore_workflow_graph_owner")
    skill = _create_skill(client, "Skill workflow pública")
    client.put(
        f"/api/skills/private/{skill['id']}/visibility",
        json={"is_public": True, "category": "Coding"},
    )
    first_agent = client.post(
        "/api/agents",
        json={
            "name": "Primer agente público",
            "scope": "public",
            "labels": ["public"],
            "skills": [skill["id"]],
            "publish_dependencies": [f"skill:{skill['id']}"],
        },
    ).json()
    second_agent = client.post(
        "/api/agents",
        json={
            "name": "Segundo agente público",
            "scope": "public",
            "labels": ["public"],
        },
    ).json()
    workflow = client.post(
        "/api/workflows",
        json={
            "name": "Workflow público con grafo",
            "labels": ["public"],
            "scope": "public",
            "definition": {
                "nodes": [
                    {"id": "one", "agent_id": first_agent["id"]},
                    {"id": "two", "agent_id": second_agent["id"]},
                ],
                "edges": [{"source": "one", "target": "two"}],
            },
        },
    )
    assert workflow.status_code in (200, 201), workflow.text
    workflow_id = workflow.json()["id"]
    publish = client.put(
        f"/api/workflows/{workflow_id}/visibility",
        json={"is_public": True, "category": "Productivity"},
    )
    assert publish.status_code == 200, publish.text

    _login(client, "explore_workflow_graph_viewer")
    response = client.get(f"/api/explore/workflow/{workflow_id}/relations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root"]["id"] == workflow_id
    items = payload["items"]
    # El flujo entre pasos y las dependencias de cada agente viajan como
    # hechos: el grafo lo arma el cliente.
    assert any(item["relation"] == "flow" for item in items)
    assert any(
        item["type"] == "skill" and item["id"] == skill["id"] for item in items
    )


def test_grafo_publico_rechaza_tipos_sin_grafo(client):
    _login(client, "explore_graph_invalid")
    response = client.get("/api/explore/skill/anything/relations")
    assert response.status_code == 422


def test_explore_muestra_antes_un_agente_recien_publicado_sin_estrellas(client):
    import asyncio

    from app.storage.db import open_db

    _login(client, "recentviewer")

    async def seed_catalog() -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO resource_social (resource_type,resource_id,owner,name,"
                "description,is_public,category,stars_count,updated_at) "
                "VALUES ('agent','popular-old','other-owner','Orden reciente antiguo',"
                "'',1,'Coding',999,'2025-01-01T00:00:00Z')"
            )
            await conn.execute(
                "INSERT INTO resource_social (resource_type,resource_id,owner,name,"
                "description,is_public,category,stars_count,updated_at) "
                "VALUES ('agent','new-no-stars','other-owner','Orden reciente nuevo',"
                "'',1,'Coding',0,'2026-01-01T00:00:00Z')"
            )
            await conn.commit()

    asyncio.run(seed_catalog())
    response = client.get("/api/explore", params={"q": "Orden reciente"})

    assert response.status_code == 200
    assert [item["resource_id"] for item in response.json()] == [
        "new-no-stars",
        "popular-old",
    ]


def test_agente_privado_desaparece_de_explore(client):
    _login(client, "exploretest2")
    agent = _create_agent(client, "Toggle Visibility Agent")
    agent_id = agent["id"]

    client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": True,
            "category": "Data",
            "trial_missing_deps": "warn",
        },
    )

    # Explore excluye los recursos propios del solicitante — se consulta como otro usuario
    _login(client, "exploretest2_viewer")
    r = client.get("/api/explore")
    assert any(x["resource_id"] == agent_id for x in r.json())

    _switch(client, "exploretest2")
    r2 = client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": False,
            "category": "Data",
            "trial_missing_deps": "warn",
        },
    )
    assert r2.status_code == 200

    _switch(client, "exploretest2_viewer")
    r3 = client.get("/api/explore")
    assert not any(x["resource_id"] == agent_id for x in r3.json())


def test_categoria_invalida_devuelve_422(client):
    _login(client, "cattest")
    agent = _create_agent(client, "Cat Test Agent")
    r = client.put(
        f"/api/agents/private/{agent['id']}/visibility",
        json={
            "is_public": True,
            "category": "InvalidCategory",
            "trial_missing_deps": "warn",
        },
    )
    assert r.status_code == 422


def test_explore_filtra_por_tipo_y_categoria(client):
    _login(client, "filtertest")
    agent = _create_agent(client, "Filter Agent Type")
    skill = _create_skill(client, "Filter Skill Type")

    client.put(
        f"/api/agents/private/{agent['id']}/visibility",
        json={
            "is_public": True,
            "category": "DevOps",
            "trial_missing_deps": "warn",
        },
    )
    client.put(
        f"/api/skills/private/{skill['id']}/visibility",
        json={
            "is_public": True,
            "category": "Writing",
        },
    )

    # Explore excluye los recursos propios del solicitante — se consulta como otro usuario
    _login(client, "filtertest_viewer")
    r_agents = client.get("/api/explore", params={"type": "agent"})
    assert r_agents.status_code == 200
    assert all(x["resource_type"] == "agent" for x in r_agents.json())
    assert any(x["resource_id"] == agent["id"] for x in r_agents.json())
    assert not any(x["resource_id"] == skill["id"] for x in r_agents.json())

    r_writing = client.get("/api/explore", params={"category": "Writing"})
    assert r_writing.status_code == 200
    assert all(x["category"] == "Writing" for x in r_writing.json())
    assert any(x["resource_id"] == skill["id"] for x in r_writing.json())


def test_explore_busqueda_por_texto(client):
    _login(client, "qtest")
    agent = _create_agent(client, "Unique Xylophone Agent")
    client.put(
        f"/api/agents/private/{agent['id']}/visibility",
        json={
            "is_public": True,
            "category": "Research",
            "trial_missing_deps": "silent",
        },
    )

    # Explore excluye los recursos propios del solicitante — se consulta como otro usuario
    _login(client, "qtest_viewer")
    r = client.get("/api/explore", params={"q": "Xylophone"})
    assert r.status_code == 200
    assert any(x["resource_id"] == agent["id"] for x in r.json())

    r2 = client.get("/api/explore", params={"q": "zzznotfoundzzz"})
    assert r2.status_code == 200
    assert not any(x["resource_id"] == agent["id"] for x in r2.json())


def test_star_unstar_actualiza_stars_count(client):
    _login(client, "startest")
    agent = _create_agent(client, "Starrable Agent")
    agent_id = agent["id"]

    client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": True,
            "category": "Finance",
            "trial_missing_deps": "warn",
        },
    )

    # Explore excluye los recursos propios del solicitante — estrellar/consultar como otro usuario
    _login(client, "startest_viewer")
    r_star = client.post(f"/api/agent/{agent_id}/star")
    assert r_star.status_code == 200
    body = r_star.json()
    assert body["ok"] is True
    assert body["stars"] == 1

    r_explore = client.get("/api/explore", params={"type": "agent", "q": "Starrable"})
    found = next(x for x in r_explore.json() if x["resource_id"] == agent_id)
    assert found["stars_count"] == 1

    r_unstar = client.delete(f"/api/agent/{agent_id}/star")
    assert r_unstar.status_code == 200
    assert r_unstar.json()["stars"] == 0

    r_explore2 = client.get("/api/explore", params={"type": "agent", "q": "Starrable"})
    found2 = next(x for x in r_explore2.json() if x["resource_id"] == agent_id)
    assert found2["stars_count"] == 0


def test_recursos_publicos_de_usuario(client):
    user = _login(client, "pubresuser")
    agent = _create_agent(client, "Public Resource Agent")
    agent_id = agent["id"]

    client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": True,
            "category": "Support",
            "trial_missing_deps": "warn",
        },
    )

    r = client.get(f"/api/users/{user}/resources")
    assert r.status_code == 200
    ids = [x["resource_id"] for x in r.json()]
    assert agent_id in ids

    r_filtered = client.get(f"/api/users/{user}/resources", params={"type": "agent"})
    assert r_filtered.status_code == 200
    assert all(x["resource_type"] == "agent" for x in r_filtered.json())

    # Compatibilidad con publicaciones anteriores a la migracion que cambio
    # resource_social.owner de username a id interno.
    import asyncio

    from app.storage.db import open_db

    async def _make_legacy_owner() -> None:
        async with open_db() as conn:
            await conn.execute(
                "UPDATE resource_social SET owner=? WHERE resource_type=? AND resource_id=?",
                (user, "agent", agent_id),
            )
            await conn.commit()

    asyncio.run(_make_legacy_owner())
    legacy = client.get(f"/api/users/{user}/resources")
    assert any(item["resource_id"] == agent_id for item in legacy.json())

    _login(client, "pubresviewer")
    users = client.get("/api/users", params={"q": user}).json()
    card = next(item for item in users if item["username"] == user)
    assert card["public_resources_count"] == 1


def test_explore_requiere_auth(client):
    r = client.get("/api/explore")
    assert r.status_code == 401


# ─── /try endpoint ────────────────────────────────────────────────────────────


def _create_connection_raw(username: str, conn_id: str = "test-conn-try-001") -> str:
    """Inserta una connection directamente en la BD para pruebas."""
    import asyncio
    import json

    from app.auth.auth import get_user_by_username
    from app.storage.db import open_db

    async def _do() -> None:
        user = await get_user_by_username(username)
        assert user is not None
        data = json.dumps(
            {
                "id": conn_id,
                "name": "Test Try Connection",
                "provider": "openai",
                "model": "gpt-4o",
            }
        )
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO connections "
                "(id, owner_id, data, tokens_in, tokens_out, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, 0, datetime('now'), datetime('now'))",
                (conn_id, user["id"], data),
            )
            await conn.commit()

    asyncio.run(_do())
    return conn_id


def _make_agent_public(client, agent_id: str) -> None:
    r = client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": True,
            "category": "Coding",
            "trial_missing_deps": "warn",
        },
    )
    assert r.status_code == 200


def test_try_agente_publico_ok(client, monkeypatch):
    user = _login(client, "trytest_ok")
    agent = _create_agent(client, "Try Agent OK")
    agent_id = agent["id"]
    _make_agent_public(client, agent_id)
    conn_id = _create_connection_raw(user, f"conn-try-ok-{agent_id[:8]}")

    async def _fake_stream(*args, **kwargs):
        yield "data: no-es-json\n\n"
        yield 'data: {"type":"chunk","content":"hola"}\n\n'
        yield 'data: {"type":"done"}\n\n'

    monkeypatch.setattr("app.api.routes.resource_linking.stream_chat", _fake_stream)
    warnings = []
    monkeypatch.setattr("app.api.routes.resource_linking.flog.warning", warnings.append)

    r = client.post(
        f"/api/agents/private/{agent_id}/try",
        json={
            "connection_id": conn_id,
            "message": "Hola agente",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "reply" in body
    assert body["reply"] == "hola"
    assert "warnings" in body
    assert isinstance(body["warnings"], list)
    assert any("Evento SSE inválido" in warning for warning in warnings)


def test_try_agente_privado_devuelve_404(client):
    user = _login(client, "trytest_priv")
    agent = _create_agent(client, "Private Try Agent")
    agent_id = agent["id"]
    conn_id = _create_connection_raw(user, f"conn-try-priv-{agent_id[:8]}")

    # No se hace público — no existe en resource_social como público
    r = client.post(
        f"/api/agents/private/{agent_id}/try",
        json={
            "connection_id": conn_id,
            "message": "Hola",
        },
    )
    assert r.status_code == 404


def test_try_connection_invalida_devuelve_400(client):
    _login(client, "trytest_conn")
    agent = _create_agent(client, "Try Agent Conn")
    agent_id = agent["id"]
    _make_agent_public(client, agent_id)

    r = client.post(
        f"/api/agents/private/{agent_id}/try",
        json={
            "connection_id": "conn-inexistente-xyz",
            "message": "Hola",
        },
    )
    assert r.status_code == 400


def test_try_sin_message_devuelve_422(client):
    user = _login(client, "trytest_422")
    agent = _create_agent(client, "Try Agent 422")
    agent_id = agent["id"]
    _make_agent_public(client, agent_id)
    conn_id = _create_connection_raw(user, f"conn-try-422-{agent_id[:8]}")

    r = client.post(
        f"/api/agents/private/{agent_id}/try",
        json={
            "connection_id": conn_id,
            # "message" ausente → 422
        },
    )
    assert r.status_code == 422


def test_try_requiere_auth(client):
    # Sin cookie de sesión → 401
    client.cookies.clear()
    r = client.post(
        "/api/agents/private/nonexistent-agent/try",
        json={
            "connection_id": "any",
            "message": "Hola",
        },
    )
    assert r.status_code == 401


def test_explore_and_feed_reject_negative_pagination(client):
    _login(client, "social_pagevalidator")
    for path in ("/api/explore", "/api/feed"):
        assert client.get(path, params={"limit": -1}).status_code == 422
        assert client.get(path, params={"offset": -1}).status_code == 422
