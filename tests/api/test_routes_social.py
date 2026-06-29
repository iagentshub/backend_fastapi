"""Tests de la Fase 2 social: visibilidad, explore, stars y recursos de usuario."""
from __future__ import annotations


def _login(client, username="socialtest", password="pass1234"):
    from app.auth.auth import create_token, register_user
    import asyncio
    asyncio.run(register_user(username, password, email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _create_agent(client, name="Social Agent"):
    r = client.post("/api/agents", json={
        "name": name,
        "description": "agente de prueba social",
        "labels": ["public"],
    })
    assert r.status_code == 200
    return r.json()


def _create_skill(client, name="Social Skill"):
    r = client.post("/api/skills/private", json={
        "name": name,
        "description": "skill de prueba social",
        "content": "# instrucciones",
        "labels": ["public"],
    })
    assert r.status_code == 200
    return r.json()


def _insert_folder(username, folder_id, folder_name="Carpeta Social"):
    import asyncio
    from app.storage.db import open_db

    async def _do() -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO knowledge_folders (id, owner_id, section, name, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (folder_id, username, "document", folder_name),
            )
            await conn.commit()

    asyncio.run(_do())


def test_agente_publico_aparece_en_explore(client):
    user = _login(client, "exploretest1")
    agent = _create_agent(client, "Explore Agent One")
    agent_id = agent["id"]

    r = client.put(f"/api/agents/private/{agent_id}/visibility", json={
        "is_public": True,
        "category": "Coding",
        "trial_missing_deps": "warn",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get("/api/explore")
    assert r.status_code == 200
    ids = [x["resource_id"] for x in r.json()]
    assert agent_id in ids

    found = next(x for x in r.json() if x["resource_id"] == agent_id)
    assert found["resource_type"] == "agent"
    assert found["owner"] == user
    assert found["category"] == "Coding"


def test_agente_privado_desaparece_de_explore(client):
    _login(client, "exploretest2")
    agent = _create_agent(client, "Toggle Visibility Agent")
    agent_id = agent["id"]

    client.put(f"/api/agents/private/{agent_id}/visibility", json={
        "is_public": True,
        "category": "Data",
        "trial_missing_deps": "warn",
    })

    r = client.get("/api/explore")
    assert any(x["resource_id"] == agent_id for x in r.json())

    r2 = client.put(f"/api/agents/private/{agent_id}/visibility", json={
        "is_public": False,
        "category": "Data",
        "trial_missing_deps": "warn",
    })
    assert r2.status_code == 200

    r3 = client.get("/api/explore")
    assert not any(x["resource_id"] == agent_id for x in r3.json())


def test_categoria_invalida_devuelve_422(client):
    _login(client, "cattest")
    agent = _create_agent(client, "Cat Test Agent")
    r = client.put(f"/api/agents/private/{agent['id']}/visibility", json={
        "is_public": True,
        "category": "InvalidCategory",
        "trial_missing_deps": "warn",
    })
    assert r.status_code == 422


def test_explore_filtra_por_tipo_y_categoria(client):
    _login(client, "filtertest")
    agent = _create_agent(client, "Filter Agent Type")
    skill = _create_skill(client, "Filter Skill Type")

    client.put(f"/api/agents/private/{agent['id']}/visibility", json={
        "is_public": True, "category": "DevOps", "trial_missing_deps": "warn",
    })
    client.put(f"/api/skills/private/{skill['id']}/visibility", json={
        "is_public": True, "category": "Writing",
    })

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
    client.put(f"/api/agents/private/{agent['id']}/visibility", json={
        "is_public": True, "category": "Research", "trial_missing_deps": "silent",
    })

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

    client.put(f"/api/agents/private/{agent_id}/visibility", json={
        "is_public": True, "category": "Finance", "trial_missing_deps": "warn",
    })

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

    client.put(f"/api/agents/private/{agent_id}/visibility", json={
        "is_public": True, "category": "Support", "trial_missing_deps": "warn",
    })

    r = client.get(f"/api/users/{user}/resources")
    assert r.status_code == 200
    ids = [x["resource_id"] for x in r.json()]
    assert agent_id in ids

    r_filtered = client.get(f"/api/users/{user}/resources", params={"type": "agent"})
    assert r_filtered.status_code == 200
    assert all(x["resource_type"] == "agent" for x in r_filtered.json())


def test_knowledge_folder_visibilidad(client):
    user = _login(client, "knowledgetest")
    folder_id = "test-folder-social-001"
    _insert_folder(user, folder_id, "Carpeta de Conocimiento")

    r = client.put(f"/api/knowledge/folders/{folder_id}/visibility", json={
        "is_public": True,
        "category": "Education",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.get("/api/explore", params={"type": "knowledge"})
    assert r2.status_code == 200
    ids = [x["resource_id"] for x in r2.json()]
    assert folder_id in ids

    found = next(x for x in r2.json() if x["resource_id"] == folder_id)
    assert found["name"] == "Carpeta de Conocimiento"
    assert found["category"] == "Education"


def test_explore_requiere_auth(client):
    r = client.get("/api/explore")
    assert r.status_code == 401


# ─── /try endpoint ────────────────────────────────────────────────────────────

def _create_connection_raw(username: str, conn_id: str = "test-conn-try-001") -> str:
    """Inserta una connection directamente en la BD para pruebas."""
    import asyncio
    import json
    from app.storage.db import open_db

    async def _do() -> None:
        data = json.dumps({
            "id": conn_id,
            "name": "Test Try Connection",
            "provider": "openai",
            "model": "gpt-4o",
        })
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO connections "
                "(id, owner_id, data, tokens_in, tokens_out, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, 0, datetime('now'), datetime('now'))",
                (conn_id, username, data),
            )
            await conn.commit()

    asyncio.run(_do())
    return conn_id


def _make_agent_public(client, agent_id: str) -> None:
    r = client.put(f"/api/agents/private/{agent_id}/visibility", json={
        "is_public": True,
        "category": "Coding",
        "trial_missing_deps": "warn",
    })
    assert r.status_code == 200


def test_try_agente_publico_ok(client, monkeypatch):
    user = _login(client, "trytest_ok")
    agent = _create_agent(client, "Try Agent OK")
    agent_id = agent["id"]
    _make_agent_public(client, agent_id)
    conn_id = _create_connection_raw(user, f"conn-try-ok-{agent_id[:8]}")

    async def _fake_stream(*args, **kwargs):
        yield 'data: {"type":"chunk","content":"hola"}\n\n'
        yield 'data: {"type":"done"}\n\n'

    monkeypatch.setattr("app.api.routes.social.stream_chat", _fake_stream)

    r = client.post(f"/api/agents/private/{agent_id}/try", json={
        "connection_id": conn_id,
        "message": "Hola agente",
    })
    assert r.status_code == 200
    body = r.json()
    assert "reply" in body
    assert body["reply"] == "hola"
    assert "warnings" in body
    assert isinstance(body["warnings"], list)


def test_try_agente_privado_devuelve_404(client):
    user = _login(client, "trytest_priv")
    agent = _create_agent(client, "Private Try Agent")
    agent_id = agent["id"]
    conn_id = _create_connection_raw(user, f"conn-try-priv-{agent_id[:8]}")

    # No se hace público — no existe en resource_social como público
    r = client.post(f"/api/agents/private/{agent_id}/try", json={
        "connection_id": conn_id,
        "message": "Hola",
    })
    assert r.status_code == 404


def test_try_connection_invalida_devuelve_400(client):
    _login(client, "trytest_conn")
    agent = _create_agent(client, "Try Agent Conn")
    agent_id = agent["id"]
    _make_agent_public(client, agent_id)

    r = client.post(f"/api/agents/private/{agent_id}/try", json={
        "connection_id": "conn-inexistente-xyz",
        "message": "Hola",
    })
    assert r.status_code == 400


def test_try_sin_message_devuelve_422(client):
    user = _login(client, "trytest_422")
    agent = _create_agent(client, "Try Agent 422")
    agent_id = agent["id"]
    _make_agent_public(client, agent_id)
    conn_id = _create_connection_raw(user, f"conn-try-422-{agent_id[:8]}")

    r = client.post(f"/api/agents/private/{agent_id}/try", json={
        "connection_id": conn_id,
        # "message" ausente → 422
    })
    assert r.status_code == 422


def test_try_requiere_auth(client):
    # Sin cookie de sesión → 401
    client.cookies.clear()
    r = client.post("/api/agents/private/nonexistent-agent/try", json={
        "connection_id": "any",
        "message": "Hola",
    })
    assert r.status_code == 401
