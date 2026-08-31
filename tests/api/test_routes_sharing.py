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
    _register("sh_badtype")
    _set_cookie(client, "sh_badtype")
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
    group = client.post("/api/groups", json={"name": "Grupo Compartir Agente"}).json()
    agent = client.post("/api/agents", json={
        "name": "Agente a compartir", "system_prompt": "p", "model": "gpt-4o",
    }).json()

    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_share_agent_visible_to_group_member(client):
    _register("sh_owner_c")
    _register("sh_member_c")

    _set_cookie(client, "sh_owner_c")
    agent = client.post("/api/agents", json={
        "name": "Agente compartido", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Visible"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "sh_member_c", "role": "member"})

    # El dueño comparte el agente con el grupo (group_id en el body)
    client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})

    # El miembro puede ver el agente en su lista general (sin filtro de grupo)
    _set_cookie(client, "sh_member_c")
    agents = client.get("/api/v2/agents").json()["items"]
    shared = next((a for a in agents if a["id"] == agent["id"]), None)
    assert shared is not None
    assert shared.get("_shared") is True


def test_share_nonexistent_resource_returns_404(client):
    _register("sh_owner_d")
    _set_cookie(client, "sh_owner_d")
    group = client.post("/api/groups", json={"name": "Grupo D"}).json()
    r = client.post("/api/sharing/agent/no-existe-este-agente",
                    json={"group_id": group["id"]})
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
    group = client.post("/api/groups", json={"name": "Grupo Bystander"}).json()
    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    assert r.status_code == 403


# ── Dejar de compartir ─────────────────────────────────────────────────────────

def test_unshare_agent_revokes_access(client):
    _register("sh_owner_g")
    _register("sh_member_g")

    _set_cookie(client, "sh_owner_g")
    agent = client.post("/api/agents", json={
        "name": "Agente a revocar", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Revoke"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "sh_member_g", "role": "member"})

    # Compartir y luego dejar de compartir (group_id como query param en DELETE)
    client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    r = client.delete(f"/api/sharing/agent/{agent['id']}?group_id={group['id']}")
    assert r.status_code == 200

    # El miembro ya no debe ver el agente
    _set_cookie(client, "sh_member_g")
    agents = client.get("/api/v2/agents").json()["items"]
    assert not any(a["id"] == agent["id"] for a in agents)


# ── Compartir skill y conocimiento ────────────────────────────────────────────

def test_share_skill_with_group(client):
    _register("sh_owner_h")
    _register("sh_member_h")

    _set_cookie(client, "sh_owner_h")
    skill = client.post("/api/skills/private", json={"name": "Skill H", "description": "d"}).json()
    group = client.post("/api/groups", json={"name": "Grupo Skill H"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "sh_member_h", "role": "member"})

    r = client.post(f"/api/sharing/skill/{skill['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200

    # El dueño sigue viendo origin_type='owner' en su propia lista
    owner_skills = client.get("/api/v2/skills?scope=all").json()["items"]
    own = next(s for s in owner_skills if s["id"] == skill["id"])
    assert own["origin_type"] == "owner"

    # El miembro ve la skill en su lista (vista general sin filtro de grupo)
    _set_cookie(client, "sh_member_h")
    skills = client.get("/api/v2/skills?scope=all").json()["items"]
    shared = next(s for s in skills if s["id"] == skill["id"])
    assert shared.get("_shared")
    assert shared["origin_type"] == "linked"


def test_share_knowledge_with_group(client):
    _register("sh_owner_i")
    _register("sh_member_i")

    _set_cookie(client, "sh_owner_i")
    know = client.post("/api/knowledge/text", json={
        "title": "Doc compartido", "content": "contenido",
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Know I"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "sh_member_i", "role": "member"})

    r = client.post(f"/api/sharing/knowledge/{know['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200

    # El miembro ve el conocimiento compartido
    _set_cookie(client, "sh_member_i")
    items = client.get("/api/v2/knowledge").json()["items"]
    assert any(k["id"] == know["id"] and k.get("_shared") for k in items)


def test_share_pack_grants_access_to_pack_and_individual_files(client, monkeypatch):
    _register("sh_pack_owner")
    _register("sh_pack_member")

    _set_cookie(client, "sh_pack_owner")
    pack = client.post(
        "/api/knowledge/packs",
        data={"name": "Pack compartido", "paths": '["docs/guide.md"]'},
        files=[("files", ("guide.md", b"# Guide", "text/markdown"))],
    ).json()
    group = client.post("/api/groups", json={"name": "Grupo Pack"}).json()
    client.post(
        f"/api/groups/{group['id']}/members",
        json={"username": "sh_pack_member", "role": "member"},
    )
    shared = client.post(
        f"/api/sharing/knowledge_pack/{pack['id']}",
        json={"group_id": group["id"]},
    )
    assert shared.status_code == 200

    _set_cookie(client, "sh_pack_member")
    from app.api.routes.knowledge import packs as packs_routes

    async def unexpected_single_pack_read(*args, **kwargs):
        raise AssertionError("shared pack listing must not read packs one by one")

    with monkeypatch.context() as patch:
        patch.setattr(packs_routes._packs, "get", unexpected_single_pack_read)
        packs = client.get("/api/v2/knowledge-packs").json()["items"]
        group_packs = client.get(
            "/api/v2/knowledge-packs", params={"group_id": group["id"]}
        ).json()["items"]
    assert any(item["id"] == pack["id"] and item.get("_shared") for item in packs)
    assert any(
        item["id"] == pack["id"] and item.get("_group_id") == group["id"]
        for item in group_packs
    )
    items = client.get("/api/v2/knowledge").json()["items"]
    pack_item = next(item for item in items if item["pack_id"] == pack["id"])
    assert pack_item["_shared_via_pack"] is True

    full_pack_agent = client.post(
        "/api/agents",
        json={
            "name": "Agente con pack",
            "system_prompt": "p",
            "model": "gpt-4o",
            "knowledge_packs": [pack["id"]],
        },
    )
    assert full_pack_agent.status_code == 200
    individual_agent = client.post(
        "/api/agents",
        json={
            "name": "Agente con archivo del pack",
            "system_prompt": "p",
            "model": "gpt-4o",
            "knowledge": [pack_item["id"]],
        },
    )
    assert individual_agent.status_code == 200


def test_share_workflow_with_group(client):
    _register("sh_workflow_owner")
    _register("sh_workflow_member")

    _set_cookie(client, "sh_workflow_owner")
    agent = client.post(
        "/api/agents",
        json={
            "name": "Agente del flujo compartido",
            "system_prompt": "Procesa la entrada",
            "model": "gpt-4o",
        },
    ).json()
    workflow = client.post(
        "/api/workflows",
        json={
            "name": "Flujo compartido",
            "definition": {
                "nodes": [{"id": "step-one", "agent_id": agent["id"]}],
                "edges": [],
            },
        },
    ).json()
    group = client.post(
        "/api/groups", json={"name": "Grupo de orquestación"}
    ).json()
    client.post(
        f"/api/groups/{group['id']}/members",
        json={"username": "sh_workflow_member", "role": "member"},
    )

    shared = client.post(
        f"/api/sharing/workflow/{workflow['id']}",
        json={"group_id": group["id"]},
    )
    assert shared.status_code == 200
    assert agent["id"] in shared.json()["cascaded"]

    # El dueño sigue viendo origin_type='owner' en su propia lista
    _set_cookie(client, "sh_workflow_owner")
    own = next(
        item for item in client.get("/api/workflows").json() if item["id"] == workflow["id"]
    )
    assert own["origin_type"] == "owner"

    _set_cookie(client, "sh_workflow_member")
    workflows = client.get("/api/workflows").json()
    visible = next(item for item in workflows if item["id"] == workflow["id"])
    assert visible["_shared"] is True
    assert visible["origin_type"] == "linked"

    forbidden = client.post("/api/workflows", json=visible)
    assert forbidden.status_code == 403
