"""Tests de aislamiento real de datos entre tenants.

Valida que los recursos de un group nunca son visibles desde otro,
ni por error ni por ausencia de filtro — independientemente del rol.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

_AGENT = {
    "name": "Agente de prueba",
    "system_prompt": "Eres un asistente de pruebas.",
    "model": "gpt-4o",
    "temperature": 0.7,
}


def _register(username: str) -> None:
    import asyncio

    from app.auth.auth import register_user
    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass


def _user_id(username: str) -> str:
    import asyncio

    from app.auth.auth import get_user_by_username

    return asyncio.run(get_user_by_username(username))["id"]


def _token(username: str, group_id: str | None = None) -> str:
    from app.auth.auth import create_token
    return create_token(username, group_id=group_id)


def _set_cookie(client: TestClient, username: str, group_id: str | None = None) -> None:
    client.cookies.set("ga_token", _token(username, group_id))


# ── Aislamiento entre groups personales ────────────────────────────────────

def test_agent_not_visible_to_other_user(client):
    """El agente de Alice NO aparece en la lista de Bob."""
    _register("iso_alice_a")
    _register("iso_bob_a")

    _set_cookie(client, "iso_alice_a")
    r = client.post("/api/agents", json={**_AGENT, "name": "Agente de Alice"})
    assert r.status_code == 200

    _set_cookie(client, "iso_bob_a")
    agents = client.get("/api/agents").json()
    names = [a["name"] for a in agents]
    assert "Agente de Alice" not in names


def test_agent_owner_id_is_group_id(client):
    """El owner_id del agente coincide con el group_id del token."""
    _register("iso_owner_b")
    _set_cookie(client, "iso_owner_b")
    r = client.post("/api/agents", json=_AGENT)
    assert r.status_code == 200
    assert r.json()["owner_id"] == _user_id("iso_owner_b")


def test_agent_created_in_team_group_has_team_owner_id(client):
    """Agentes creados en group de equipo tienen el ID del equipo como owner."""
    _register("iso_team_creator_c")
    _set_cookie(client, "iso_team_creator_c")
    group = client.post("/api/groups", json={"name": "Equipo ISO"}).json()

    # Cambia al group de equipo
    _set_cookie(client, "iso_team_creator_c", group_id=group["id"])
    r = client.post("/api/agents", json={**_AGENT, "name": "Agente Equipo"})
    assert r.status_code == 200
    assert r.json()["owner_id"] == group["id"]


def test_team_agent_visible_to_all_members(client):
    """Un agente creado en el group de equipo es visible para todos sus miembros."""
    _register("iso_creator_d")
    _register("iso_member_d")

    _set_cookie(client, "iso_creator_d")
    group = client.post("/api/groups", json={"name": "Equipo Visible"}).json()

    # Añade a member
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "iso_member_d", "role": "member"})

    # Crea agente en el group de equipo
    _set_cookie(client, "iso_creator_d", group_id=group["id"])
    client.post("/api/agents", json={**_AGENT, "name": "Agente Compartido"})

    # Member también puede verlo al cambiar al mismo group
    _set_cookie(client, "iso_member_d", group_id=group["id"])
    agents = client.get("/api/agents").json()
    names = [a["name"] for a in agents]
    assert "Agente Compartido" in names


def test_team_agent_not_visible_outside_team(client):
    """El agente del equipo NO aparece en el group personal de ningún miembro."""
    _register("iso_creator_e")
    _register("iso_member_e")

    _set_cookie(client, "iso_creator_e")
    group = client.post("/api/groups", json={"name": "Equipo Privado"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "iso_member_e", "role": "member"})

    _set_cookie(client, "iso_creator_e", group_id=group["id"])
    client.post("/api/agents", json={**_AGENT, "name": "Solo del Equipo"})

    # En group personal del creador → NO visible
    _set_cookie(client, "iso_creator_e")
    agents = client.get("/api/agents").json()
    assert not any(a["name"] == "Solo del Equipo" for a in agents)

    # En group personal del miembro → NO visible
    _set_cookie(client, "iso_member_e")
    agents = client.get("/api/agents").json()
    assert not any(a["name"] == "Solo del Equipo" for a in agents)


def test_outsider_cannot_see_team_agent(client):
    """Un usuario ajeno al equipo no ve los agentes del equipo aunque conozca el ID."""
    _register("iso_creator_f")
    _register("iso_outsider_f")

    _set_cookie(client, "iso_creator_f")
    group = client.post("/api/groups", json={"name": "Equipo Cerrado"}).json()

    _set_cookie(client, "iso_creator_f", group_id=group["id"])
    agent = client.post("/api/agents", json={**_AGENT, "name": "Secreto"}).json()

    # El ajeno no puede cambiar al group del equipo
    _set_cookie(client, "iso_outsider_f")
    r = client.post(f"/api/groups/switch/{group['id']}")
    assert r.status_code == 403

    # Aunque fuerze el token con el group_id del equipo, los filtros lo bloquean
    _set_cookie(client, "iso_outsider_f", group_id=group["id"])
    agents = client.get("/api/agents").json()
    assert not any(a["id"] == agent["id"] for a in agents)


def test_delete_agent_from_other_group_rejected(client):
    """Un usuario no puede eliminar agentes de otro group."""
    _register("iso_del_alice_g")
    _register("iso_del_bob_g")

    _set_cookie(client, "iso_del_alice_g")
    agent = client.post("/api/agents", json=_AGENT).json()

    _set_cookie(client, "iso_del_bob_g")
    r = client.delete(f"/api/agents/{agent['id']}")
    # 404 (no visible) o 403 (visto pero sin permisos) — ambos válidos
    assert r.status_code in (403, 404)


# ── Aislamiento de /me ─────────────────────────────────────────────────────────

def test_me_returns_group_id(client):
    """GET /api/auth/me incluye group_id en la respuesta."""
    _register("iso_me_h")
    _set_cookie(client, "iso_me_h")
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["group_id"] == _user_id("iso_me_h")
    assert data["group_personal"] is True


def test_me_returns_team_group_after_switch(client):
    """Tras cambiar de group, /me refleja el nuevo group_id."""
    _register("iso_me_switch_i")
    _set_cookie(client, "iso_me_switch_i")
    group = client.post("/api/groups", json={"name": "Equipo Me"}).json()

    _set_cookie(client, "iso_me_switch_i", group_id=group["id"])
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["group_id"] == group["id"]
    assert data["group_personal"] is False


# ── Aislamiento de skills ─────────────────────────────────────────────────────

def test_private_skill_not_visible_to_other_user(client):
    """La skill privada de Alice no aparece en la lista de Bob."""
    _register("iso_skill_alice_j")
    _register("iso_skill_bob_j")

    _set_cookie(client, "iso_skill_alice_j")
    r = client.post("/api/skills/private", json={"name": "Skill Secreta", "description": "desc"})
    assert r.status_code == 200

    _set_cookie(client, "iso_skill_bob_j")
    skills = client.get("/api/skills?scope=private").json()
    assert not any(s["name"] == "Skill Secreta" for s in skills)


def test_team_skill_visible_to_members(client):
    """Una skill privada creada en group de equipo es visible para los miembros."""
    _register("iso_skill_creator_k")
    _register("iso_skill_member_k")

    _set_cookie(client, "iso_skill_creator_k")
    group = client.post("/api/groups", json={"name": "Equipo Skills"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "iso_skill_member_k", "role": "member"})

    _set_cookie(client, "iso_skill_creator_k", group_id=group["id"])
    r = client.post("/api/skills/private", json={"name": "Skill Equipo", "description": "desc"})
    assert r.status_code == 200

    _set_cookie(client, "iso_skill_member_k", group_id=group["id"])
    skills = client.get("/api/skills?scope=all").json()
    assert any(s["name"] == "Skill Equipo" for s in skills)
