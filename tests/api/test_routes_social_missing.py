"""Tests de cobertura adicionales para app/api/routes/social.py."""

from __future__ import annotations

import asyncio
from uuid import uuid4


# ── helpers ───────────────────────────────────────────────────────────────────


def _uid(prefix: str = "u") -> str:
    return f"{prefix}{uuid4().hex[:8]}"


def _login(client, username: str, password: str = "pass1234") -> str:
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, password, email=f"{username}@test.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _make_public_agent(client, name: str) -> str:
    """Crea un agente (labels=public) y lo registra como público en resource_social."""
    r = client.post(
        "/api/agents",
        json={
            "name": name,
            "description": "agent for social tests",
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]
    rv = client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": True,
            "category": "Coding",
            "trial_missing_deps": "warn",
        },
    )
    assert rv.status_code == 200
    return agent_id


def _make_public_skill(client, name: str) -> str:
    """Crea una skill (labels=public) y la registra como pública en resource_social."""
    r = client.post(
        "/api/skills/private",
        json={
            "name": name,
            "description": "skill for social tests",
            "content": "# skill content",
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]
    rv = client.put(
        f"/api/skills/private/{skill_id}/visibility",
        json={
            "is_public": True,
            "category": "Coding",
        },
    )
    assert rv.status_code == 200
    return skill_id


def _insert_public_knowledge(username: str, title: str | None = None) -> str:
    """Inserta un knowledge item en la BD y en resource_social como público."""
    import app.config.data as cfg
    from app.storage.db import open_db
    from app.storage.knowledge import KnowledgeStorage

    if title is None:
        title = f"Test Knowledge {uuid4().hex[:6]}"

    async def _do() -> str:
        ks = KnowledgeStorage(cfg.DB_FILE)
        result = await ks.save(
            type="url",
            title=title,
            source="https://example.com",
            content="test knowledge content for social tests",
            owner_id=username,
        )
        kid: str = result["id"]
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, "
                "category, trial_missing_deps, tags, labels, updated_at) "
                "VALUES (?, ?, ?, ?, '', 1, 'Other', 'warn', '[]', '[\"public\"]', datetime('now'))",
                ("knowledge", kid, username, title),
            )
            await conn.commit()
        return kid

    return asyncio.run(_do())


def _insert_private_knowledge(username: str, title: str | None = None) -> str:
    """Inserta un knowledge item en la BD SIN añadirlo a resource_social como público."""
    import app.config.data as cfg
    from app.storage.knowledge import KnowledgeStorage

    if title is None:
        title = f"Private Knowledge {uuid4().hex[:6]}"

    async def _do() -> str:
        ks = KnowledgeStorage(cfg.DB_FILE)
        result = await ks.save(
            type="url",
            title=title,
            source="https://example.com",
            content="private knowledge content",
            owner_id=username,
        )
        return result["id"]

    return asyncio.run(_do())


def _insert_social_row(
    username: str,
    resource_type: str,
    resource_id: str,
    name: str,
    is_public: int = 0,
    linked_to_user: str | None = None,
    linked_to_id: str | None = None,
) -> None:
    """Inserta directamente una fila en resource_social."""
    from app.storage.db import open_db

    async def _do() -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, "
                "category, trial_missing_deps, tags, labels, "
                "linked_to_user, linked_to_id, updated_at) "
                "VALUES (?, ?, ?, ?, '', ?, 'Other', 'warn', '[]', '[\"private\"]', ?, ?, datetime('now'))",
                (
                    resource_type,
                    resource_id,
                    username,
                    name,
                    is_public,
                    linked_to_user,
                    linked_to_id,
                ),
            )
            await conn.commit()

    asyncio.run(_do())


def _register(username: str) -> None:
    from app.auth.auth import register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))


# ── set_agent_visibility — líneas 102, 106 ────────────────────────────────────


def test_set_agent_visibility_trial_missing_deps_invalido(client):
    """Línea 102: trial_missing_deps con valor inválido devuelve 422."""
    _login(client, _uid("vis1"))
    r = client.post(
        "/api/agents",
        json={
            "name": f"VisTrialAgent {uuid4().hex[:6]}",
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]
    rv = client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={
            "is_public": True,
            "category": "Coding",
            "trial_missing_deps": "invalido",
        },
    )
    assert rv.status_code == 422


def test_set_agent_visibility_agente_inexistente(client):
    """Línea 106: agente no encontrado devuelve 404."""
    _login(client, _uid("vis2"))
    rv = client.put(
        "/api/agents/private/agente-no-existe-xyz999/visibility",
        json={
            "is_public": True,
            "category": "Coding",
            "trial_missing_deps": "warn",
        },
    )
    assert rv.status_code == 404


# ── set_skill_visibility — línea 143 ─────────────────────────────────────────


def test_set_skill_visibility_skill_inexistente(client):
    """Línea 143: skill no encontrada devuelve 404."""
    _login(client, _uid("skvis"))
    rv = client.put(
        "/api/skills/private/skill-no-existe-xyz999/visibility",
        json={
            "is_public": True,
            "category": "Coding",
        },
    )
    assert rv.status_code == 404


# ── explore con label filter — líneas 230-231 ────────────────────────────────


def test_explore_filtra_por_label_existente(client):
    """Líneas 230-231: filtro ?label=public devuelve el recurso con esa label."""
    _login(client, _uid("explbl"))
    agent_id = _make_public_agent(client, f"LabelFilterAgent {uuid4().hex[:6]}")

    # Explore excluye los recursos propios del solicitante, así que se consulta
    # como otro usuario distinto del dueño del agente.
    _login(client, _uid("explbl_viewer"))
    r = client.get("/api/explore", params={"label": "public"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    ids = [x["resource_id"] for x in r.json()]
    assert agent_id in ids


def test_explore_filtra_por_label_inexistente(client):
    """Líneas 230-231: label que no existe devuelve lista vacía."""
    _login(client, _uid("explbl2"))
    r = client.get("/api/explore", params={"label": "label-never-exist-xyz999"})
    assert r.status_code == 200
    assert r.json() == []


# ── explore_preview — líneas 266-324 ─────────────────────────────────────────


def test_explore_preview_agente_ok(client):
    """Líneas 266-306: preview de agente público devuelve campos del agente."""
    _login(client, _uid("prv1"))
    agent_id = _make_public_agent(client, f"PreviewAgent {uuid4().hex[:6]}")

    r = client.get(f"/api/explore/agent/{agent_id}/preview")
    assert r.status_code == 200
    data = r.json()
    assert data["resource_type"] == "agent"
    assert data["resource_id"] == agent_id
    assert "system_prompt" in data
    assert "skills" in data


def test_explore_preview_skill_ok(client):
    """Líneas 307-314: preview de skill pública devuelve campos de la skill."""
    _login(client, _uid("prv2"))
    skill_id = _make_public_skill(client, f"PreviewSkill {uuid4().hex[:6]}")

    r = client.get(f"/api/explore/skill/{skill_id}/preview")
    assert r.status_code == 200
    data = r.json()
    assert data["resource_type"] == "skill"
    assert data["resource_id"] == skill_id


def test_explore_preview_knowledge_ok(client):
    """Líneas 315-323: preview de knowledge público devuelve content."""
    user = _login(client, _uid("prv3"))
    kid = _insert_public_knowledge(user)

    r = client.get(f"/api/explore/knowledge/{kid}/preview")
    assert r.status_code == 200
    data = r.json()
    assert data["resource_type"] == "knowledge"
    assert data["resource_id"] == kid
    assert "content" in data


def test_explore_preview_no_encontrado(client):
    """Líneas 273-274: recurso inexistente devuelve 404."""
    _login(client, _uid("prv4"))
    r = client.get("/api/explore/agent/recurso-no-existe-xyz999/preview")
    assert r.status_code == 404


def test_explore_preview_no_publico(client):
    """Líneas 266-274: recurso no público devuelve 404."""
    _login(client, _uid("prv5"))
    r = client.post("/api/agents", json={"name": f"PrivPreview {uuid4().hex[:6]}"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    # No se llama a visibility → no aparece en resource_social como público
    r2 = client.get(f"/api/explore/agent/{agent_id}/preview")
    assert r2.status_code == 404


# ── my_resources — líneas 327-376 ────────────────────────────────────────────


def test_mis_recursos_sin_filtro(client):
    """Líneas 327-376: GET /api/social/me/resources devuelve recursos propios."""
    _login(client, _uid("myres"))
    agent_id = _make_public_agent(client, f"MyResAgent {uuid4().hex[:6]}")

    r = client.get("/api/social/me/resources")
    assert r.status_code == 200
    data = r.json()
    assert "resources" in data
    ids = [x["resource_id"] for x in data["resources"]]
    assert agent_id in ids


def test_mis_recursos_con_filtro_tipo_agent(client):
    """Líneas 338-339: filtro ?type=agent en my_resources."""
    _login(client, _uid("mytyp"))
    agent_id = _make_public_agent(client, f"MyTypeAgent {uuid4().hex[:6]}")

    r = client.get("/api/social/me/resources", params={"type": "agent"})
    assert r.status_code == 200
    data = r.json()
    assert "resources" in data
    assert all(x["resource_type"] == "agent" for x in data["resources"])
    assert any(x["resource_id"] == agent_id for x in data["resources"])


def test_mis_recursos_filtro_tipo_excluye_otros(client):
    """Líneas 338-339: filtro ?type=skill excluye agentes."""
    _login(client, _uid("mytyp2"))
    agent_id = _make_public_agent(client, f"ExcludeTypeAgent {uuid4().hex[:6]}")

    r = client.get("/api/social/me/resources", params={"type": "skill"})
    assert r.status_code == 200
    ids = [x["resource_id"] for x in r.json()["resources"]]
    assert agent_id not in ids


def test_mis_recursos_vacio(client):
    """Líneas 349-360: usuario sin recursos registrados → lista vacía."""
    _login(client, _uid("emptyres"))
    r = client.get("/api/social/me/resources")
    assert r.status_code == 200
    assert r.json() == {"resources": []}


# ── user_resources — líneas 379-413 ──────────────────────────────────────────


def test_user_resources_con_filtro_tipo(client):
    """Líneas 406-411: GET /api/users/{user}/resources?type=agent."""
    user = _login(client, _uid("ures"))
    agent_id = _make_public_agent(client, f"UserResAgent {uuid4().hex[:6]}")

    r = client.get(f"/api/users/{user}/resources", params={"type": "agent"})
    assert r.status_code == 200
    items = r.json()
    assert all(x["resource_type"] == "agent" for x in items)
    assert any(x["resource_id"] == agent_id for x in items)


# ── feed con filtro tipo — líneas 485-527 ────────────────────────────────────


def _insert_feed_resource(
    owner: str, resource_id: str, resource_type: str = "agent"
) -> None:
    from app.storage.db import open_db

    async def _do() -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, "
                "category, trial_missing_deps, tags, labels, updated_at) "
                "VALUES (?, ?, ?, ?, '', 1, 'Coding', 'warn', '[]', '[\"public\"]', datetime('now'))",
                (
                    resource_type,
                    resource_id,
                    owner,
                    f"Feed {resource_type} {resource_id[:6]}",
                ),
            )
            await conn.commit()

    asyncio.run(_do())


def test_feed_con_filtro_tipo_agent(client):
    """Líneas 501-502: feed con ?type=agent excluye skills."""
    _login(client, _uid("fftf"))
    followed = _uid("fftf_tgt")
    _register(followed)

    rid_agent = f"feed-ag-{uuid4().hex[:10]}"
    rid_skill = f"feed-sk-{uuid4().hex[:10]}"
    _insert_feed_resource(followed, rid_agent, "agent")
    _insert_feed_resource(followed, rid_skill, "skill")

    client.post(f"/api/users/{followed}/follow")

    r = client.get("/api/feed", params={"type": "agent"})
    assert r.status_code == 200
    items = r.json()
    assert all(x["resource_type"] == "agent" for x in items)
    assert any(x["resource_id"] == rid_agent for x in items)
    assert not any(x["resource_id"] == rid_skill for x in items)


# ── link_knowledge — líneas 620-671 ──────────────────────────────────────────


def test_link_knowledge_ok(client):
    """Líneas 620-671: link de knowledge pública."""
    owner = _login(client, _uid("lkw1"))
    title = f"Linkable Knowledge {uuid4().hex[:6]}"
    kid = _insert_public_knowledge(owner, title=title)

    linker = _uid("lkw1_lnk")
    _login(client, linker)
    r = client.post(f"/api/knowledge/{kid}/link")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "knowledge_id" in data
    assert data["name"] == title


def test_link_knowledge_inexistente(client):
    """Líneas 625-627: knowledge no encontrado → 404."""
    _login(client, _uid("lkw2"))
    r = client.post("/api/knowledge/knowledge-link-no-existe-xyz999/link")
    assert r.status_code == 404


def test_link_knowledge_no_publico(client):
    """Líneas 630-637: knowledge existe pero no es público → 403."""
    user = _login(client, _uid("lkw3"))
    kid = _insert_private_knowledge(user)
    r = client.post(f"/api/knowledge/{kid}/link")
    assert r.status_code == 403


# ── link_agent — líneas 807-871 ──────────────────────────────────────────────


def test_link_agent_ok(client):
    """Líneas 807-871: link de agente público → 200."""
    _login(client, _uid("lagok"))
    agent_name = f"LinkableAgent {uuid4().hex[:6]}"
    agent_id = _make_public_agent(client, agent_name)

    linker = _uid("lagok_lnk")
    _login(client, linker)
    r = client.post(f"/api/agents/private/{agent_id}/link")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "agent_id" in data
    assert data["name"] == agent_name


def test_link_agent_no_encontrado(client):
    """Líneas 814-816: agente no encontrado → 404."""
    _login(client, _uid("lagnf"))
    r = client.post("/api/agents/private/agente-link-no-existe-xyz999/link")
    assert r.status_code == 404


def test_link_agent_no_publico(client):
    """Líneas 818-826: agente existe pero no es público en social → 403."""
    _login(client, _uid("lagpriv"))
    r = client.post("/api/agents", json={"name": f"NotPublicLink {uuid4().hex[:6]}"})
    assert r.status_code == 200
    agent_id = r.json()["id"]

    linker = _uid("lagpriv_lnk")
    _login(client, linker)
    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 403


# ── link_skill — líneas 874-935 ──────────────────────────────────────────────


def test_link_skill_ok(client):
    """Líneas 874-935: link de skill pública → 200."""
    _login(client, _uid("lskillok"))
    skill_name = f"LinkableSkill {uuid4().hex[:6]}"
    skill_id = _make_public_skill(client, skill_name)

    linker = _uid("lskillok_lnk")
    _login(client, linker)
    r = client.post(f"/api/skills/private/{skill_id}/link")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "skill_id" in data
    assert data["name"] == skill_name


def test_link_skill_no_encontrada(client):
    """Línea 901: skill no encontrada → 404."""
    _login(client, _uid("lskillnf"))
    r = client.post("/api/skills/private/skill-link-no-existe-xyz999/link")
    assert r.status_code == 404


def test_link_skill_no_publica(client):
    """Líneas 885-893: skill existe pero no es pública en social → 403."""
    _login(client, _uid("lskpriv"))
    r = client.post(
        "/api/skills/private",
        json={
            "name": f"NotPublicLinkSkill {uuid4().hex[:6]}",
            "description": "desc",
            "content": "# skill",
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]

    linker = _uid("lskpriv_lnk")
    _login(client, linker)
    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 403


# ── sync_linked_agent — líneas 938-967 ───────────────────────────────────────


def test_sync_linked_agent_no_encontrado(client):
    """Líneas 944-946: agente no encontrado o no es del usuario → 404."""
    _login(client, _uid("syncagnf"))
    r = client.post("/api/agents/private/agente-sync-no-existe-xyz999/sync")
    assert r.status_code == 404


def test_sync_linked_agent_sin_enlace(client):
    """Líneas 955-956: agente existe pero sin linked_to_id → 400."""
    user = _login(client, _uid("syncag"))
    r = client.post("/api/agents", json={"name": f"SyncAgent {uuid4().hex[:6]}"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    # Insertar en resource_social sin linked_to_id (NULL)
    _insert_social_row(user, "agent", agent_id, f"SyncAgent {agent_id}", is_public=0)

    r2 = client.post(f"/api/agents/private/{agent_id}/sync")
    assert r2.status_code == 400
    assert "enlace" in r2.json()["detail"]["message"].lower()


# ── sync_linked_skill — líneas 970-999 ───────────────────────────────────────


def test_sync_linked_skill_no_encontrada(client):
    """Líneas 977-978: skill no encontrada o no es del usuario → 404."""
    _login(client, _uid("syncsk2"))
    r = client.post("/api/skills/private/skill-sync-no-existe-xyz999/sync")
    assert r.status_code == 404


def test_sync_linked_skill_sin_enlace(client):
    """Líneas 986-988: skill existe pero sin linked_to_id → 400."""
    user = _login(client, _uid("syncsk"))
    r = client.post(
        "/api/skills/private",
        json={
            "name": f"SyncSkill {uuid4().hex[:6]}",
            "description": "desc",
            "content": "# skill",
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _insert_social_row(user, "skill", skill_id, f"SyncSkill {skill_id}", is_public=0)

    r2 = client.post(f"/api/skills/private/{skill_id}/sync")
    assert r2.status_code == 400

