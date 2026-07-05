"""Tests de /api/sharing — compartir un recurso con un grupo de trabajo.

El sharing requiere `group_id` en el cuerpo del POST (o como query param en el DELETE).
No copia ni mueve el recurso: solo concede acceso de uso al grupo indicado.
"""
from __future__ import annotations


def _register(username: str) -> None:
    import asyncio
    from app.auth.auth import register_user
    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass


def _token(username: str) -> str:
    from app.auth.auth import create_token
    return create_token(username)


def _set_cookie(client, username: str) -> None:
    client.cookies.set("ga_token", _token(username))


# ── Auth requerida ────────────────────────────────────────────────────────────

def test_post_sharing_requires_auth(client):
    r = client.post("/api/sharing/agent/resource-id")
    assert r.status_code == 401


def test_delete_sharing_requires_auth(client):
    r = client.delete("/api/sharing/agent/resource-id")
    assert r.status_code == 401


# ── Validación de tipo de recurso ─────────────────────────────────────────────

def test_invalid_resource_type_returns_422(client):
    _register("sh_a")
    _set_cookie(client, "sh_a")
    # El tipo se valida antes de leer el body; no hace falta pasar group_id
    r = client.post("/api/sharing/badtype/resource-id")
    assert r.status_code == 422


# ── group_id es obligatorio ───────────────────────────────────────────────────

def test_post_sharing_without_group_id_returns_400(client):
    _register("sh_no_group")
    _set_cookie(client, "sh_no_group")
    agent = client.post("/api/agents", json={
        "name": "Agente sin grupo", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    r = client.post(f"/api/sharing/agent/{agent['id']}", json={})
    assert r.status_code == 400


# ── Compartir agente con un grupo ─────────────────────────────────────────────

def test_share_agent_with_group_success(client):
    _register("sh_owner_b")
    _set_cookie(client, "sh_owner_b")
    ws = client.post("/api/workspaces", json={"name": "WS Compartir Agente"}).json()
    agent = client.post("/api/agents", json={
        "name": "Agente a compartir", "system_prompt": "p", "model": "gpt-4o",
    }).json()

    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": ws["id"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_share_agent_visible_to_group_member(client):
    _register("sh_owner_c")
    _register("sh_member_c")

    _set_cookie(client, "sh_owner_c")
    agent = client.post("/api/agents", json={
        "name": "Agente compartido", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    ws = client.post("/api/workspaces", json={"name": "WS Visible"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "sh_member_c", "role": "member"})

    # El dueño comparte el agente con el grupo (group_id en el body)
    client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": ws["id"]})

    # El miembro puede ver el agente en su lista general (sin filtro de grupo)
    _set_cookie(client, "sh_member_c")
    agents = client.get("/api/agents").json()
    shared = next((a for a in agents if a["id"] == agent["id"]), None)
    assert shared is not None
    assert shared.get("_shared") is True


def test_share_nonexistent_resource_returns_404(client):
    _register("sh_owner_d")
    _set_cookie(client, "sh_owner_d")
    ws = client.post("/api/workspaces", json={"name": "WS D"}).json()
    r = client.post("/api/sharing/agent/no-existe-este-agente",
                    json={"group_id": ws["id"]})
    assert r.status_code == 404


def test_share_resource_without_ownership_forbidden(client):
    _register("sh_owner_e")
    _register("sh_bystander_e")

    _set_cookie(client, "sh_owner_e")
    agent = client.post("/api/agents", json={
        "name": "Agente ajeno", "system_prompt": "p", "model": "gpt-4o",
    }).json()

    # El intruso crea su propio grupo e intenta compartir el agente ajeno
    _set_cookie(client, "sh_bystander_e")
    ws = client.post("/api/workspaces", json={"name": "WS Bystander"}).json()
    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": ws["id"]})
    assert r.status_code == 403


# ── Dejar de compartir ─────────────────────────────────────────────────────────

def test_unshare_agent_revokes_access(client):
    _register("sh_owner_g")
    _register("sh_member_g")

    _set_cookie(client, "sh_owner_g")
    agent = client.post("/api/agents", json={
        "name": "Agente a revocar", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    ws = client.post("/api/workspaces", json={"name": "WS Revoke"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "sh_member_g", "role": "member"})

    # Compartir y luego dejar de compartir (group_id como query param en DELETE)
    client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": ws["id"]})
    r = client.delete(f"/api/sharing/agent/{agent['id']}?group_id={ws['id']}")
    assert r.status_code == 200

    # El miembro ya no debe ver el agente
    _set_cookie(client, "sh_member_g")
    agents = client.get("/api/agents").json()
    assert not any(a["id"] == agent["id"] for a in agents)


# ── Compartir skill y conocimiento ────────────────────────────────────────────

def test_share_skill_with_group(client):
    _register("sh_owner_h")
    _register("sh_member_h")

    _set_cookie(client, "sh_owner_h")
    skill = client.post("/api/skills/private", json={"name": "Skill H", "description": "d"}).json()
    ws = client.post("/api/workspaces", json={"name": "WS Skill H"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "sh_member_h", "role": "member"})

    r = client.post(f"/api/sharing/skill/{skill['id']}", json={"group_id": ws["id"]})
    assert r.status_code == 200

    # El miembro ve la skill en su lista (vista general sin filtro de grupo)
    _set_cookie(client, "sh_member_h")
    skills = client.get("/api/skills?scope=all").json()
    assert any(s["id"] == skill["id"] and s.get("_shared") for s in skills)


def test_share_knowledge_with_group(client):
    _register("sh_owner_i")
    _register("sh_member_i")

    _set_cookie(client, "sh_owner_i")
    know = client.post("/api/knowledge/text", json={
        "title": "Doc compartido", "content": "contenido",
    }).json()
    ws = client.post("/api/workspaces", json={"name": "WS Know I"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "sh_member_i", "role": "member"})

    r = client.post(f"/api/sharing/knowledge/{know['id']}", json={"group_id": ws["id"]})
    assert r.status_code == 200

    # El miembro ve el conocimiento compartido
    _set_cookie(client, "sh_member_i")
    items = client.get("/api/knowledge").json()
    assert any(k["id"] == know["id"] and k.get("_shared") for k in items)
