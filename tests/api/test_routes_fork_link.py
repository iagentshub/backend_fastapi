"""Tests de link y sync de agentes y skills (endpoints sociales)."""
from __future__ import annotations

import json


def _login(client, username: str) -> str:
    from app.auth.auth import create_token, register_user
    import asyncio
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
# fork label tests
# ---------------------------------------------------------------------------

def test_fork_agente_tiene_label_fork(client):
    owner = _login(client, "linktest01")
    r = client.post("/api/agents", json={"name": "Agente Para Fork Label", "description": "d"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    r2 = client.post(f"/api/agents/private/{agent_id}/fork")
    assert r2.status_code == 200
    fork_id = r2.json()["agent_id"]

    # Verificar que el agente forkeado tiene la label 'fork'
    r3 = client.get(f"/api/agents/{fork_id}")
    assert r3.status_code == 200
    labels = r3.json().get("labels", [])
    assert "fork" in labels


def test_fork_skill_tiene_label_fork(client):
    owner = _login(client, "linktest02")
    r = client.post("/api/skills/private", json={
        "name": "Skill Para Fork Label",
        "description": "d",
        "content": "# skill",
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    r2 = client.post(f"/api/skills/private/{skill_id}/fork")
    assert r2.status_code == 200
    fork_id = r2.json()["skill_id"]

    r3 = client.get(f"/api/skills/private/{fork_id}")
    assert r3.status_code == 200
    labels = r3.json().get("labels", [])
    assert "fork" in labels


# ---------------------------------------------------------------------------
# link agent
# ---------------------------------------------------------------------------

def test_link_agente_publico_devuelve_200(client):
    owner = _login(client, "linktest03")
    r = client.post("/api/agents", json={"name": "Agente Linkeable", "description": "d"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert "agent_id" in body
    assert body["name"] == "Agente Linkeable"


def test_link_agente_tiene_label_linked(client):
    owner = _login(client, "linktest04")
    r = client.post("/api/agents", json={"name": "Agente Label Linked", "description": "d"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["agent_id"]

    r3 = client.get(f"/api/agents/{link_id}")
    assert r3.status_code == 200
    labels = r3.json().get("labels", [])
    assert "linked" in labels
    assert "fork" not in labels


def test_link_agente_guarda_linked_to_en_resource_social(client):
    owner = _login(client, "linktest05")
    r = client.post("/api/agents", json={"name": "Agente Social Link", "description": "d"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["agent_id"]

    row = _get_social_row("agent", link_id)
    assert row.get("linked_to_id") == agent_id
    assert row.get("linked_to_user") == owner
    # No debe tener fork_of_id
    assert not row.get("fork_of_id")


def test_link_agente_no_publico_devuelve_403(client):
    _login(client, "linktest06")
    r = client.post("/api/agents", json={"name": "Agente Privado Link", "description": "d"})
    assert r.status_code == 200
    agent_id = r.json()["id"]

    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 403


def test_link_agente_inexistente_devuelve_404(client):
    _login(client, "linktest07")
    r = client.post("/api/agents/private/no-existe-este-agente/link")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# link skill
# ---------------------------------------------------------------------------

def test_link_skill_publico_devuelve_200(client):
    owner = _login(client, "linktest08")
    r = client.post("/api/skills/private", json={
        "name": "Skill Linkeable",
        "description": "d",
        "content": "# skill",
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert "skill_id" in body
    assert body["name"] == "Skill Linkeable"


def test_link_skill_tiene_label_linked(client):
    owner = _login(client, "linktest09")
    r = client.post("/api/skills/private", json={
        "name": "Skill Label Linked",
        "description": "d",
        "content": "# skill",
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

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
    r = client.post("/api/skills/private", json={
        "name": "Skill Social Link",
        "description": "d",
        "content": "# skill",
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["skill_id"]

    row = _get_social_row("skill", link_id)
    assert row.get("linked_to_id") == skill_id
    assert row.get("linked_to_user") == owner
    assert not row.get("fork_of_id")


def test_link_skill_no_publico_devuelve_403(client):
    _login(client, "linktest11")
    r = client.post("/api/skills/private", json={
        "name": "Skill Privada Link",
        "description": "d",
        "content": "# skill",
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]

    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 403


def test_link_skill_inexistente_devuelve_404(client):
    _login(client, "linktest12")
    r = client.post("/api/skills/private/no-existe-esta-skill/link")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# sync agent
# ---------------------------------------------------------------------------

def _create_public_agent_via_filesystem(name: str) -> str:
    """Crea un agente público directamente en el filesystem (scope=public)."""
    from app.config.data import AGENTS_DIR
    import uuid
    agent_id = str(uuid.uuid4())
    sys_dir = AGENTS_DIR / "public" / agent_id
    sys_dir.mkdir(parents=True, exist_ok=True)
    (sys_dir / "config.json").write_text(
        json.dumps({
            "id": agent_id,
            "name": name,
            "scope": "public",
            "description": "descripcion original",
            "system_prompt": "prompt original",
        }),
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
    r = client.post("/api/agents", json={"name": "Agente Sin Enlace", "description": "d"})
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
    from app.config.data import SKILLS_DIR
    import uuid
    skill_id = str(uuid.uuid4())
    sys_dir = SKILLS_DIR / "public" / skill_id
    sys_dir.mkdir(parents=True, exist_ok=True)
    (sys_dir / "SKILL.md").write_text("# skill original\ncontenido original", encoding="utf-8")
    (sys_dir / "config.json").write_text(
        json.dumps({
            "id": skill_id,
            "name": name,
            "scope": "public",
            "description": "desc original",
        }),
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
    r = client.post("/api/skills/private", json={
        "name": "Skill Sin Enlace",
        "description": "d",
        "content": "# skill",
    })
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
    from app.storage.db import open_db

    agent_id = "verify-agent-adm-001"
    agent_owner = "testadmin"

    async def _insert():
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

    r2 = admin_client.get("/api/explore", params={"type": "agent"})
    assert r2.status_code == 200
    found = next((x for x in r2.json() if x["resource_id"] == agent_id), None)
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
    r = client.post("/api/agents", json={"name": "LB Original Agent", "description": "d"})
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
# convert-to-fork
# ---------------------------------------------------------------------------

def test_convert_agent_link_to_fork(client):
    """Convertir un link de agente a fork: linked_to_id=None, fork_of_id=original."""
    owner = _login(client, "ctftest01")
    r = client.post("/api/agents", json={"name": "Agente Para Convertir", "description": "d"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(agent_id, owner)

    # Enlazar
    r2 = client.post(f"/api/agents/private/{agent_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["agent_id"]

    # Verificar que tiene linked_to_id
    row_before = _get_social_row("agent", link_id)
    assert row_before.get("linked_to_id") == agent_id

    # Convertir a fork
    r3 = client.post(f"/api/agents/private/{link_id}/link/convert-to-fork")
    assert r3.status_code == 200
    assert r3.json()["ok"] is True
    assert r3.json()["agent_id"] == link_id

    # Verificar que linked_to_id es None y fork_of_id tiene el original
    row_after = _get_social_row("agent", link_id)
    assert not row_after.get("linked_to_id")
    assert row_after.get("fork_of_id") == agent_id
    assert row_after.get("fork_of_user") == owner

    # El agente en disco debe tener label 'fork' y no 'linked'
    r4 = client.get(f"/api/agents/{link_id}")
    assert r4.status_code == 200
    labels = r4.json().get("labels", [])
    assert "fork" in labels
    assert "linked" not in labels


def test_convert_skill_link_to_fork(client):
    """Convertir un link de skill a fork: linked_to_id=None, fork_of_id=original."""
    owner = _login(client, "ctftest02")
    r = client.post("/api/skills/private", json={
        "name": "Skill Para Convertir",
        "description": "d",
        "content": "# skill",
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(skill_id, owner)

    # Enlazar
    r2 = client.post(f"/api/skills/private/{skill_id}/link")
    assert r2.status_code == 200
    link_id = r2.json()["skill_id"]

    row_before = _get_social_row("skill", link_id)
    assert row_before.get("linked_to_id") == skill_id

    # Convertir a fork
    r3 = client.post(f"/api/skills/private/{link_id}/link/convert-to-fork")
    assert r3.status_code == 200
    assert r3.json()["ok"] is True
    assert r3.json()["skill_id"] == link_id

    row_after = _get_social_row("skill", link_id)
    assert not row_after.get("linked_to_id")
    assert row_after.get("fork_of_id") == skill_id
    assert row_after.get("fork_of_user") == owner

    r4 = client.get(f"/api/skills/private/{link_id}")
    assert r4.status_code == 200
    labels = r4.json().get("labels", [])
    assert "fork" in labels
    assert "linked" not in labels


def test_convert_link_sin_enlace_devuelve_404(client):
    """convert-to-fork devuelve 404 si el agente no tiene linked_to_id."""
    _login(client, "ctftest03")
    r = client.post("/api/agents", json={"name": "Agente Sin Enlace CTF", "description": "d"})
    assert r.status_code == 200
    agent_id = r.json()["id"]

    r2 = client.post(f"/api/agents/private/{agent_id}/link/convert-to-fork")
    assert r2.status_code == 404
