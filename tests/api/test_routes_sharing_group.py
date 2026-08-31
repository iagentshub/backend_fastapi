"""Tests de la lógica de grupos en el sharing.

Cubre:
- Filtro ?group_id en GET /api/agents (incluido para admins — bug ORANGE-JAZZTEL)
- Cascade al compartir un agente (skills y knowledge privados)
- Admin puede compartir recursos de otros usuarios
- El agente compartido aparece en GET /api/agents?group_id=... inmediatamente
"""
from __future__ import annotations

import asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

def _register(username: str, role: str = "member") -> None:
    from app.auth.auth import register_user
    from app.storage.db import open_db
    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass
    if role == "admin":
        async def _promote():
            async with open_db() as conn:
                await conn.execute(
                    "UPDATE users SET role = ? WHERE username = ?",
                    ("admin", username),
                )
                await conn.commit()
        asyncio.run(_promote())


def _token(username: str) -> str:
    from app.auth.auth import create_token
    return create_token(username)


def _set_cookie(client, username: str) -> None:
    client.cookies.set("ga_token", _token(username))


# ── Aparece en filtro de grupo inmediatamente tras compartir ──────────────────

def test_shared_agent_appears_in_group_filter(client):
    """Flujo completo: compartir → GET ?group_id → el agente aparece."""
    _register("grp_owner_1")
    _register("grp_member_1")

    _set_cookie(client, "grp_owner_1")
    agent = client.post("/api/agents", json={
        "name": "Agente filtro grupo", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Filtro"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "grp_member_1", "role": "member"})

    # Compartir el agente con el grupo
    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200, r.text

    # El dueño puede consultarlo con filtro de grupo
    result = client.get(f"/api/v2/agents?group_id={group['id']}").json()["items"]
    assert any(a["id"] == agent["id"] for a in result), (
        "El agente compartido no aparece en GET /api/agents?group_id=..."
    )


def test_group_filter_excludes_unshared_agents(client):
    """GET ?group_id no devuelve agentes que NO están compartidos con ese grupo."""
    _register("grp_owner_2")
    _register("grp_member_2")

    _set_cookie(client, "grp_owner_2")
    # Agente compartido
    a_shared = client.post("/api/agents", json={
        "name": "Compartido", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    # Agente NO compartido
    a_private = client.post("/api/agents", json={
        "name": "Privado", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Exclusion"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "grp_member_2", "role": "member"})

    client.post(f"/api/sharing/agent/{a_shared['id']}", json={"group_id": group["id"]})

    result = client.get(f"/api/v2/agents?group_id={group['id']}").json()["items"]
    ids = {a["id"] for a in result}
    assert a_shared["id"] in ids, "El agente compartido debe aparecer"
    assert a_private["id"] not in ids, "El agente no compartido no debe aparecer"


# ── Filtro de grupo aplicado también a admins (bug ORANGE-JAZZTEL) ────────────

def test_admin_group_filter_excludes_unshared_agents(client):
    """Un admin que filtra por grupo solo ve los agentes compartidos con ese grupo,
    no todos los agentes del sistema — fix del bug ORANGE-JAZZTEL."""
    _register("grp_admin_3", role="admin")
    _register("grp_owner_3")
    _register("grp_member_3")

    # El dueño crea dos agentes y comparte solo uno
    _set_cookie(client, "grp_owner_3")
    a_shared = client.post("/api/agents", json={
        "name": "Compartido con grupo", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    a_private = client.post("/api/agents", json={
        "name": "Solo del dueño", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Admin Test"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "grp_member_3", "role": "member"})
    client.post(f"/api/sharing/agent/{a_shared['id']}", json={"group_id": group["id"]})

    # El admin filtra por grupo — debe ver SOLO el compartido, no el privado del dueño
    _set_cookie(client, "grp_admin_3")
    result = client.get(f"/api/v2/agents?group_id={group['id']}").json()["items"]
    ids = {a["id"] for a in result}
    assert a_shared["id"] in ids, "El agente compartido debe aparecer para el admin"
    assert a_private["id"] not in ids, (
        "El agente no compartido NO debe aparecer para el admin cuando hay filtro de grupo"
    )


def test_admin_does_not_see_others_private_agents_without_group_filter(client):
    """Sin filtro de grupo, el listado personal del admin NO debe incluir
    agentes privados ajenos sin marcar como compartidos — eso exponía datos
    privados de otros usuarios en la vista personal del admin. La visibilidad
    global de admin se sirve por el endpoint dedicado /api/admin/agents."""
    _register("grp_admin_4", role="admin")
    _register("grp_owner_4")

    _set_cookie(client, "grp_owner_4")
    agent = client.post("/api/agents", json={
        "name": "Agente para admin", "system_prompt": "p", "model": "gpt-4o",
    }).json()

    _set_cookie(client, "grp_admin_4")
    result = client.get("/api/v2/agents").json()["items"]
    assert not any(a["id"] == agent["id"] for a in result), (
        "Sin filtro de grupo, el admin NO debe ver agentes privados ajenos "
        "en su listado personal"
    )


def test_tool_under_review_cannot_be_shared_directly_or_via_agent(client):
    _register("grp_tool_owner")
    _set_cookie(client, "grp_tool_owner")
    tool = client.post(
        "/api/tools/private",
        json={
            "name": "Tool pendiente",
            "language": "python",
            "content": "print('ok')",
        },
    ).json()
    agent = client.post(
        "/api/agents",
        json={"name": "Agente con Tool", "tools": [tool["id"]]},
    ).json()
    group = client.post("/api/groups", json={"name": "Grupo Tool"}).json()

    direct = client.post(
        f"/api/sharing/tool/{tool['id']}", json={"group_id": group["id"]}
    )
    cascade = client.post(
        f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]}
    )

    assert direct.status_code == 403
    assert cascade.status_code == 403
    assert direct.json()["detail"]["labels"] == ["review"]


# ── Admin puede compartir recursos ajenos ─────────────────────────────────────

def test_admin_can_share_others_agent(client):
    """Un admin puede compartir un agente que no le pertenece."""
    _register("grp_admin_5", role="admin")
    _register("grp_owner_5")
    _register("grp_member_5")

    # El dueño crea el agente
    _set_cookie(client, "grp_owner_5")
    agent = client.post("/api/agents", json={
        "name": "Agente del dueño", "system_prompt": "p", "model": "gpt-4o",
    }).json()

    # El admin crea un grupo y comparte el agente ajeno
    _set_cookie(client, "grp_admin_5")
    group = client.post("/api/groups", json={"name": "Grupo Admin"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "grp_member_5", "role": "member"})

    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200, f"Admin debería poder compartir: {r.text}"

    # El miembro puede ver el agente con filtro de grupo
    _set_cookie(client, "grp_member_5")
    result = client.get(f"/api/v2/agents?group_id={group['id']}").json()["items"]
    assert any(a["id"] == agent["id"] for a in result)


# ── Cascade: compartir agente comparte skills y knowledge ─────────────────────

def test_cascade_shares_private_skills(client):
    """Al compartir un agente, sus skills privadas se comparten automáticamente."""
    _register("grp_cas_6")
    _register("grp_member_6")

    _set_cookie(client, "grp_cas_6")
    skill = client.post("/api/skills/private", json={
        "name": "Skill privada cascada", "description": "d",
    }).json()
    agent = client.post("/api/agents", json={
        "name": "Agente con skill", "system_prompt": "p", "model": "gpt-4o",
        "skills": [skill["id"]],
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Cascade Skills"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "grp_member_6", "role": "member"})

    # Compartir el agente — debe hacer cascade en la skill privada
    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200
    cascaded = r.json().get("cascaded", [])
    assert skill["id"] in cascaded, (
        f"La skill privada debería estar en cascaded: {cascaded}"
    )

    # El miembro puede ver la skill con filtro de grupo
    _set_cookie(client, "grp_member_6")
    skills = client.get(f"/api/v2/skills?group_id={group['id']}").json()["items"]
    assert any(s["id"] == skill["id"] for s in skills), (
        "La skill no aparece en el filtro de grupo tras el cascade"
    )


def test_cascade_shares_knowledge(client):
    """Al compartir un agente, su knowledge se comparte automáticamente."""
    _register("grp_cas_7")
    _register("grp_member_7")

    _set_cookie(client, "grp_cas_7")
    know = client.post("/api/knowledge/text", json={
        "title": "Conocimiento cascada", "content": "contenido de prueba",
    }).json()
    agent = client.post("/api/agents", json={
        "name": "Agente con knowledge", "system_prompt": "p", "model": "gpt-4o",
        "knowledge": [know["id"]],
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Cascade Know"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "grp_member_7", "role": "member"})

    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200
    cascaded = r.json().get("cascaded", [])
    assert know["id"] in cascaded, (
        f"El knowledge debería estar en cascaded: {cascaded}"
    )

    # El miembro puede ver el knowledge con filtro de grupo
    _set_cookie(client, "grp_member_7")
    items = client.get(f"/api/v2/knowledge?group_id={group['id']}").json()["items"]
    assert any(k["id"] == know["id"] for k in items), (
        "El knowledge no aparece en el filtro de grupo tras el cascade"
    )


def test_cascade_does_not_share_public_skills(client):
    """Las skills públicas (scope='public') no se incluyen en cascaded.

    Las skills públicas son de solo lectura y ya son accesibles para todos,
    así que el cascade las omite. Se insertan directamente en la BD porque
    la API de skills no permite crear skills públicas (son globales del sistema).
    """
    _register("grp_cas_8")

    # Insertar una skill pública directamente en la BD (las skills públicas
    # son de solo lectura vía API — pertenecen al sistema, no a usuarios)
    import asyncio as _asyncio
    import json as _json

    from app.storage.db import open_db

    skill_id = "pub_skill_test_8"

    async def _insert_public_skill():
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO skills (id, owner_id, scope, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (skill_id, "__public__", "public",
                 _json.dumps({"name": "Skill pública", "id": skill_id}), now, now),
            )
            await conn.commit()

    _asyncio.run(_insert_public_skill())

    _set_cookie(client, "grp_cas_8")
    agent = client.post("/api/agents", json={
        "name": "Agente skill pública", "system_prompt": "p", "model": "gpt-4o",
        "skills": [skill_id],
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo Cascade Publicas"}).json()

    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200
    cascaded = r.json().get("cascaded", [])
    assert skill_id not in cascaded, (
        "Las skills públicas no deberían ser parte del cascade"
    )


def test_cascade_does_not_share_connections(client):
    """Las conexiones NUNCA se comparten en cascada (contienen credenciales)."""
    _register("grp_cas_9")

    _set_cookie(client, "grp_cas_9")
    # Solo verificamos que el endpoint no devuelve connection_id en cascaded
    conn = client.post("/api/connections", json={
        "name": "Conn privada", "provider": "openai",
        "api_key": "sk-test", "scope": "personal",
    }).json()
    agent = client.post("/api/agents", json={
        "name": "Agente con conexion", "system_prompt": "p", "model": "gpt-4o",
        "connection_id": conn.get("id"),
    }).json()
    group = client.post("/api/groups", json={"name": "Grupo No Conn"}).json()

    r = client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group["id"]})
    assert r.status_code == 200
    cascaded = r.json().get("cascaded", [])
    if conn.get("id"):
        assert conn["id"] not in cascaded, "Las conexiones NO deben estar en cascaded"


# ── Listar grupos en que está compartido un recurso ───────────────────────────

def test_list_groups_for_shared_resource(client):
    """GET /api/sharing/agent/{id}/groups devuelve los grupos en que está compartido."""
    _register("grp_lst_10")

    _set_cookie(client, "grp_lst_10")
    agent = client.post("/api/agents", json={
        "name": "Agente listar grupos", "system_prompt": "p", "model": "gpt-4o",
    }).json()
    group1 = client.post("/api/groups", json={"name": "Grupo Lista 1"}).json()
    group2 = client.post("/api/groups", json={"name": "Grupo Lista 2"}).json()

    client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group1["id"]})
    client.post(f"/api/sharing/agent/{agent['id']}", json={"group_id": group2["id"]})

    r = client.get(f"/api/sharing/agent/{agent['id']}/groups")
    assert r.status_code == 200
    group_ids = set(r.json()["group_ids"])
    assert group1["id"] in group_ids
    assert group2["id"] in group_ids


def test_list_groups_empty_when_not_shared(client):
    """Un agente nuevo no compartido devuelve group_ids vacío."""
    _register("grp_lst_11")

    _set_cookie(client, "grp_lst_11")
    agent = client.post("/api/agents", json={
        "name": "Agente sin grupos", "system_prompt": "p", "model": "gpt-4o",
    }).json()

    r = client.get(f"/api/sharing/agent/{agent['id']}/groups")
    assert r.status_code == 200
    assert r.json()["group_ids"] == []
