"""Tests de link y sync de agentes y skills (endpoints sociales)."""

from __future__ import annotations

import json


def _login(client, username: str) -> str:
    import asyncio

    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@link.test"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _make_agent_public(agent_id: str, owner: str) -> None:
    import asyncio

    from app.storage.db import open_db

    async def _do() -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, is_public, category, trial_missing_deps) "
                "VALUES (?, ?, ?, ?, 1, 'Coding', 'warn')",
                ("agent", agent_id, owner, agent_id),
            )
            await conn.commit()

    asyncio.run(_do())


def _make_skill_public(skill_id: str, owner: str) -> None:
    import asyncio

    from app.storage.db import open_db

    async def _do() -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, is_public, category, trial_missing_deps) "
                "VALUES (?, ?, ?, ?, 1, 'Coding', 'warn')",
                ("skill", skill_id, owner, skill_id),
            )
            await conn.commit()

    asyncio.run(_do())


def _get_social_row(resource_type: str, resource_id: str) -> dict:
    import asyncio

    from app.storage.db import open_db

    async def _do() -> dict:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM resource_social WHERE resource_type = ? AND resource_id = ?",
                (resource_type, resource_id),
            )
            return dict(row) if row else {}

    return asyncio.run(_do())


# ---------------------------------------------------------------------------
# link agent
# ---------------------------------------------------------------------------


def test_link_agente_publico_devuelve_200(client):
    owner = _login(client, "linktest03")
    r = client.post(
        "/api/agents", json={"name": "Agente Linkeable", "description": "d"}
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    _login(client, "linktest03b")
    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert "agent_id" in body
    assert body["name"] == "Agente Linkeable"


def test_link_agente_tiene_label_linked(client):
    owner = _login(client, "linktest04")
    r = client.post(
        "/api/agents", json={"name": "Agente Label Linked", "description": "d"}
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    _login(client, "linktest04b")
    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["agent_id"]

    r3 = client.get(f"/api/agents/{link_id}")
    assert r3.status_code == 200
    labels = r3.json().get("labels", [])
    assert "linked" in labels
    assert "fork" not in labels


def test_link_agente_no_copia_conexiones_privadas(client):
    owner = _login(client, "link_connection_owner")
    created = client.post(
        "/api/agents",
        json={
            "name": "Agente con conexión privada",
            "connection_id": "private-connection-id",
            "op_connections": ["other-private-connection"],
            "scope": "public",
            "labels": ["public"],
            "publish_dependencies": [],
        },
    )
    assert created.status_code == 200, created.text
    source_id = created.json()["id"]
    _make_agent_public(source_id, owner)

    _login(client, "link_connection_viewer")
    linked = client.post(f"/api/agents/public/{source_id}/link")
    assert linked.status_code == 200, linked.text
    detail = client.get(f"/api/agents/{linked.json()['agent_id']}")

    assert detail.status_code == 200
    assert detail.json().get("connection_id") in (None, "")
    assert detail.json().get("op_connections") == []


def test_link_agente_legacy_no_hereda_tool_en_revision(client):
    import asyncio

    from app.api.routes.resource_linking._shared import _agents_store

    owner = _login(client, "link_reviewed_tool_owner")
    tool = client.post(
        "/api/tools/private",
        json={
            "name": "Tool legacy retenida",
            "language": "python",
            "content": "print('review')",
        },
    ).json()
    agent = client.post(
        "/api/agents",
        json={"name": "Agente legacy con Tool", "tools": [tool["id"]]},
    ).json()

    async def select_legacy_dependency() -> None:
        current = await _agents_store.get(agent["id"])
        assert current is not None
        await _agents_store.save(
            {
                **current,
                "public_dependencies": [f"tool:{tool['id']}"],
            },
            "private",
            owner_id=str(current["owner_id"]),
        )

    asyncio.run(select_legacy_dependency())
    _make_agent_public(agent["id"], owner)

    _login(client, "link_reviewed_tool_viewer")
    linked = client.post(f"/api/agents/private/{agent['id']}/link")

    assert linked.status_code == 403
    assert linked.json()["detail"]["labels"] == ["review"]


def test_link_agente_es_solo_lectura(client):
    owner = _login(client, "linkreadonly01")
    created = client.post(
        "/api/agents",
        json={"name": "Agente solo lectura", "description": "original"},
    )
    assert created.status_code == 200
    source_id = created.json()["id"]
    _make_agent_public(source_id, owner)

    _login(client, "linkreadonly02")
    linked = client.post(f"/api/agents/private/{source_id}/link")
    assert linked.status_code == 200
    link_id = linked.json()["agent_id"]

    detail = client.get(f"/api/agents/{link_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["origin_type"] == "linked"
    payload["name"] = "Intento de edición"

    edited = client.post("/api/agents", json=payload)
    assert edited.status_code == 403
    assert edited.json()["detail"]["code"] == "linked_resource_read_only"

    deactivated = client.post(f"/api/agents/{link_id}/deactivate")
    assert deactivated.status_code == 403
    group = client.post("/api/groups", json={"name": "Equipo enlace"})
    assert group.status_code == 200
    shared = client.post(
        f"/api/sharing/agent/{link_id}",
        json={"group_id": group.json()["id"]},
    )
    assert shared.status_code == 403
    deleted = client.delete(f"/api/agents/{link_id}")
    assert deleted.status_code == 403


def test_fork_agente_se_puede_editar_compartir_y_borrar(client):
    _login(client, "forkpermissions01")
    created = client.post(
        "/api/agents",
        json={
            "name": "Fork gestionable",
            "description": "copia",
            "labels": ["private", "community", "fork"],
        },
    )
    assert created.status_code == 200
    fork = created.json()
    assert fork["labels"] == ["private", "community", "fork"]

    fork["name"] = "Fork editado"
    edited = client.post("/api/agents", json=fork)
    assert edited.status_code == 200
    assert edited.json()["name"] == "Fork editado"

    group = client.post("/api/groups", json={"name": "Equipo Fork"})
    assert group.status_code == 200
    shared = client.post(
        f"/api/sharing/agent/{fork['id']}",
        json={"group_id": group.json()["id"]},
    )
    assert shared.status_code == 200

    deleted = client.delete(f"/api/agents/{fork['id']}")
    assert deleted.status_code == 200


def test_link_agente_guarda_linked_to_en_resource_social(client):
    owner = _login(client, "linktest05")
    r = client.post(
        "/api/agents", json={"name": "Agente Social Link", "description": "d"}
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    _login(client, "linktest05b")
    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["agent_id"]

    row = _get_social_row("agent", link_id)
    assert row.get("linked_to_id") == agent_id
    import asyncio

    from app.auth.auth import get_user_by_username

    assert row.get("linked_to_user") == asyncio.run(get_user_by_username(owner))["id"]


def test_link_agente_no_publico_devuelve_403(client):
    _login(client, "linktest06")
    r = client.post(
        "/api/agents", json={"name": "Agente Privado Link", "description": "d"}
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]

    _login(client, "linktest06b")
    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 403


def test_link_agente_propio_devuelve_400(client):
    owner = _login(client, "linktest06c")
    r = client.post(
        "/api/agents", json={"name": "Agente Propio Link", "description": "d"}
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 400


def test_link_agente_inexistente_devuelve_404(client):
    _login(client, "linktest07")
    r = client.post("/api/agents/private/no-existe-este-agente/link")
    assert r.status_code == 404


def test_link_agente_copia_no_conserva_label_public(client):
    """La copia enlazada no debe conservar la label 'public' del original —
    si la conservara, un guardado trivial del linker la volvería a publicar
    como una entrada duplicada del original en Explorar."""
    owner = _login(client, "linktest07b")
    r = client.post(
        "/api/agents",
        json={
            "name": "Agente Publico Con Label",
            "description": "d",
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    _login(client, "linktest07c")
    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["agent_id"]

    r3 = client.get(f"/api/agents/{link_id}")
    assert r3.status_code == 200
    labels = r3.json().get("labels", [])
    assert "public" not in labels
    assert "linked" in labels

    # Y aunque alguien intentase publicarla de todas formas, el backend lo rechaza
    r4 = client.put(
        f"/api/agents/private/{link_id}/visibility",
        json={"is_public": True, "category": "Coding", "trial_missing_deps": "warn"},
    )
    assert r4.status_code == 403


# ---------------------------------------------------------------------------
# link skill
# ---------------------------------------------------------------------------


def test_link_skill_publico_devuelve_200(client):
    owner = _login(client, "linktest08")
    r = client.post(
        "/api/skills/private",
        json={
            "name": "Skill Linkeable",
            "description": "d",
            "content": "# skill",
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    _login(client, "linktest08b")
    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert "skill_id" in body
    assert body["name"] == "Skill Linkeable"


def test_link_skill_tiene_label_linked(client):
    owner = _login(client, "linktest09")
    r = client.post(
        "/api/skills/private",
        json={
            "name": "Skill Label Linked",
            "description": "d",
            "content": "# skill",
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    _login(client, "linktest09b")
    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["skill_id"]

    r3 = client.get(f"/api/skills/private/{link_id}")
    assert r3.status_code == 200
    labels = r3.json().get("labels", [])
    assert "linked" in labels
    assert "fork" not in labels


def test_link_skill_guarda_linked_to_en_resource_social(client):
    owner = _login(client, "linktest10")
    r = client.post(
        "/api/skills/private",
        json={
            "name": "Skill Social Link",
            "description": "d",
            "content": "# skill",
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    _login(client, "linktest10b")
    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["skill_id"]

    row = _get_social_row("skill", link_id)
    assert row.get("linked_to_id") == skill_id
    import asyncio

    from app.auth.auth import get_user_by_username

    assert row.get("linked_to_user") == asyncio.run(get_user_by_username(owner))["id"]


def test_link_skill_no_publico_devuelve_403(client):
    _login(client, "linktest11")
    r = client.post(
        "/api/skills/private",
        json={
            "name": "Skill Privada Link",
            "description": "d",
            "content": "# skill",
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]

    _login(client, "linktest11b")
    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 403


def test_link_skill_propio_devuelve_400(client):
    owner = _login(client, "linktest11c")
    r = client.post(
        "/api/skills/private",
        json={
            "name": "Skill Propia Link",
            "description": "d",
            "content": "# skill",
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 400


def test_link_skill_inexistente_devuelve_404(client):
    _login(client, "linktest12")
    r = client.post("/api/skills/private/no-existe-esta-skill/link")
    assert r.status_code == 404


def test_link_skill_copia_no_conserva_label_public(client):
    """La copia enlazada de una skill no debe conservar la label 'public'."""
    owner = _login(client, "linktest12b")
    r = client.post(
        "/api/skills/private",
        json={
            "name": "Skill Publica Con Label",
            "description": "d",
            "content": "# skill",
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    _login(client, "linktest12c")
    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["skill_id"]

    r3 = client.get(f"/api/skills/private/{link_id}")
    assert r3.status_code == 200
    labels = r3.json().get("labels", [])
    assert "public" not in labels
    assert "linked" in labels

    r4 = client.put(
        f"/api/skills/private/{link_id}/visibility",
        json={"is_public": True, "category": "Coding"},
    )
    assert r4.status_code == 403


# ---------------------------------------------------------------------------
# sync agent
# ---------------------------------------------------------------------------


def _create_public_agent_via_filesystem(name: str) -> str:
    """Crea un agente público directamente en el filesystem (scope=public)."""
    import uuid

    from app.config.data import AGENTS_DIR

    agent_id = str(uuid.uuid4())
    sys_dir = AGENTS_DIR / "public" / agent_id
    sys_dir.mkdir(parents=True, exist_ok=True)
    (sys_dir / "config.json").write_text(
        json.dumps(
            {
                "id": agent_id,
                "name": name,
                "scope": "public",
                "description": "descripcion original",
                "system_prompt": "prompt original",
            }
        ),
        encoding="utf-8",
    )
    return agent_id


def test_sync_agente_enlazado_actualiza_campos(client):
    _login(client, "synctest01")

    # Crear el agente original público en el filesystem
    original_id = _create_public_agent_via_filesystem("Original Sync Agent")

    # Crear el agente enlazado via link (scope=public no requiere resource_social)
    r = client.post(f"/api/agents/public/{original_id}/link")
    assert r.status_code == 200
    link_id = r.json()["agent_id"]

    # Sincronizar
    r2 = client.post(f"/api/agents/private/{link_id}/sync")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert body["synced_from"] == original_id


def test_sync_agente_sin_enlace_devuelve_400(client):
    _login(client, "synctest02")
    r = client.post(
        "/api/agents", json={"name": "Agente Sin Enlace", "description": "d"}
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]

    # No hay fila en resource_social con linked_to_id
    r2 = client.post(f"/api/agents/private/{agent_id}/sync")
    assert r2.status_code == 400


def test_sync_agente_inexistente_devuelve_404(client):
    _login(client, "synctest03")
    r = client.post("/api/agents/private/no-existe/sync")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# sync skill
# ---------------------------------------------------------------------------


def _create_public_skill_via_filesystem(name: str) -> str:
    """Crea una skill pública directamente en el filesystem (scope=public)."""
    import uuid

    from app.config.data import SKILLS_DIR

    skill_id = str(uuid.uuid4())
    sys_dir = SKILLS_DIR / "public" / skill_id
    sys_dir.mkdir(parents=True, exist_ok=True)
    (sys_dir / "SKILL.md").write_text(
        "# skill original\ncontenido original", encoding="utf-8"
    )
    (sys_dir / "config.json").write_text(
        json.dumps(
            {
                "id": skill_id,
                "name": name,
                "scope": "public",
                "description": "desc original",
            }
        ),
        encoding="utf-8",
    )
    return skill_id


def test_sync_skill_enlazada_actualiza_campos(client):
    _login(client, "synctest04")

    original_id = _create_public_skill_via_filesystem("Original Sync Skill")

    r = client.post(f"/api/skills/public/{original_id}/link")
    assert r.status_code == 200
    link_id = r.json()["skill_id"]

    r2 = client.post(f"/api/skills/private/{link_id}/sync")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert body["synced_from"] == original_id


def test_sync_skill_sin_enlace_devuelve_400(client):
    _login(client, "synctest05")
    r = client.post(
        "/api/skills/private",
        json={
            "name": "Skill Sin Enlace",
            "description": "d",
            "content": "# skill",
        },
    )
    assert r.status_code == 200
    skill_id = r.json()["id"]

    r2 = client.post(f"/api/skills/private/{skill_id}/sync")
    assert r2.status_code == 400


def test_sync_skill_inexistente_devuelve_404(client):
    _login(client, "synctest06")
    r = client.post("/api/skills/private/no-existe/sync")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# verified
# ---------------------------------------------------------------------------


def test_verified_admin_puede_marcar(admin_client):
    """Admin marca un recurso como verified; explore devuelve verified=True."""
    import asyncio

    from app.config.data import AGENTS_DIR
    from app.storage.agent_storage import AgentStorage
    from app.storage.db import open_db

    agent_id = "verify-agent-adm-001"
    agent_owner = "someone_else_verify_test"

    async def _insert():
        await AgentStorage(AGENTS_DIR).save(
            {"id": agent_id, "name": "Verified Agent Test", "labels": ["public"]},
            owner_id=agent_owner,
        )
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, is_public, category, trial_missing_deps) "
                "VALUES (?, ?, ?, ?, 1, 'Coding', 'warn')",
                ("agent", agent_id, agent_owner, "Verified Agent Test"),
            )
            await conn.commit()

    asyncio.run(_insert())

    r = admin_client.put(
        f"/api/admin/resources/agent/{agent_id}/verify",
        json={"verified": True},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = admin_client.get("/api/v2/explore", params={"type": "agent"})
    assert r2.status_code == 200
    found = next((x for x in r2.json()["items"] if x["resource_id"] == agent_id), None)
    assert found is not None
    assert bool(found.get("verified")) is True


def test_verified_resource_type_invalido_devuelve_422(admin_client):
    r = admin_client.put(
        "/api/admin/resources/unknown/some-id/verify",
        json={"verified": True},
    )
    assert r.status_code == 422


def test_verified_recurso_inexistente_devuelve_404(admin_client):
    r = admin_client.put(
        "/api/admin/resources/agent/no-existe-este-recurso/verify",
        json={"verified": True},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# linked_broken
# ---------------------------------------------------------------------------


def test_linked_broken_detectado(client):
    """Si el original se hace privado, linked_broken=True en my_resources del enlazador."""
    import asyncio

    from app.storage.db import open_db

    # Usuario A crea el agente vía HTTP para que exista en disco
    _login(client, "lb_usera2")
    r = client.post(
        "/api/agents", json={"name": "LB Original Agent", "description": "d"}
    )
    assert r.status_code == 200
    agent_id = r.json()["id"]

    # Insertar en resource_social como público (owner=lb_usera2)
    _make_agent_public(agent_id, "lb_usera2")

    # Usuario B enlaza el agente de A
    _login(client, "lb_userb2")
    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["agent_id"]

    # linked_broken debe ser False (original sigue público)
    r3 = client.get("/api/social/me/resources")
    assert r3.status_code == 200
    resources = r3.json()["resources"]
    linked_row = next((x for x in resources if x["resource_id"] == link_id), None)
    assert linked_row is not None
    assert linked_row.get("linked_broken") is False

    # Usuario A hace privado su agente (elimina la fila de resource_social)
    async def _make_private():
        async with open_db() as conn:
            await conn.execute(
                "DELETE FROM resource_social WHERE resource_type=? AND resource_id=? AND owner=?",
                ("agent", agent_id, "lb_usera2"),
            )
            await conn.commit()

    asyncio.run(_make_private())

    # Ahora linked_broken debe ser True
    r4 = client.get("/api/social/me/resources")
    assert r4.status_code == 200
    resources2 = r4.json()["resources"]
    linked_row2 = next((x for x in resources2 if x["resource_id"] == link_id), None)
    assert linked_row2 is not None
    assert linked_row2.get("linked_broken") is True


# ---------------------------------------------------------------------------
# link workflow — la copia no debe conservar la label 'public'
# ---------------------------------------------------------------------------


def _make_workflow_public(workflow_id: str, owner: str) -> None:
    import asyncio

    from app.storage.db import open_db

    async def _do() -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, is_public, category, trial_missing_deps) "
                "VALUES (?, ?, ?, ?, 1, 'Other', 'warn')",
                ("workflow", workflow_id, owner, workflow_id),
            )
            await conn.commit()

    asyncio.run(_do())


def test_link_workflow_copia_no_conserva_label_public(client):
    """La copia enlazada de una orquestación no debe conservar la label 'public'."""
    owner = _login(client, "linktest13")
    r_agent = client.post(
        "/api/agents", json={"name": "Agente Workflow Link", "description": "d"}
    )
    assert r_agent.status_code == 200
    agent_id = r_agent.json()["id"]

    r = client.post(
        "/api/workflows",
        json={
            "name": "Workflow Publico Con Label",
            "description": "d",
            "definition": {"nodes": [{"id": "n1", "agent_id": agent_id}], "edges": []},
            "labels": ["public"],
        },
    )
    assert r.status_code == 200
    workflow_id = r.json()["id"]
    _make_workflow_public(workflow_id, owner)

    _login(client, "linktest13b")
    r2 = client.post(f"/api/workflows/{workflow_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["workflow_id"]

    r3 = client.get(f"/api/workflows/{link_id}")
    assert r3.status_code == 200
    labels = r3.json().get("labels", [])
    assert "public" not in labels
    assert "linked" in labels

    r4 = client.put(
        f"/api/workflows/{link_id}/visibility",
        json={"is_public": True, "category": "Other"},
    )
    assert r4.status_code == 403


def test_link_workflow_propio_devuelve_400(client):
    owner = _login(client, "linktest14")
    r_agent = client.post(
        "/api/agents", json={"name": "Agente Workflow Propio", "description": "d"}
    )
    assert r_agent.status_code == 200
    agent_id = r_agent.json()["id"]

    r = client.post(
        "/api/workflows",
        json={
            "name": "Workflow Propio",
            "description": "d",
            "definition": {"nodes": [{"id": "n1", "agent_id": agent_id}], "edges": []},
        },
    )
    assert r.status_code == 200
    workflow_id = r.json()["id"]
    _make_workflow_public(workflow_id, owner)

    r2 = client.post(f"/api/workflows/{workflow_id}/link")
    assert r2.status_code == 400
