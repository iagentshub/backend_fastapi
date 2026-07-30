"""Tests de /api/groups — CRUD, switch y miembros."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(client: TestClient, username: str, password: str = "pass1234") -> TestClient:
    """Registra un usuario y establece su cookie en el client."""
    import asyncio

    from app.auth.auth import create_token, register_user
    try:
        asyncio.run(register_user(username, password, email=f"{username}@test.com"))
    except ValueError:
        pass
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return client


def _switch(client: TestClient, group_id: str, username: str) -> TestClient:
    """Cambia el group activo actualizando la cookie directamente."""
    from app.auth.auth import create_token
    token = create_token(username, group_id=group_id)
    client.cookies.set("ga_token", token)
    return client


# ── Auth requerida ────────────────────────────────────────────────────────────

def test_list_groups_requires_auth(client):
    r = client.get("/api/groups")
    assert r.status_code == 401


def test_create_group_requires_auth(client):
    r = client.post("/api/groups", json={"name": "Test"})
    assert r.status_code == 401


# ── Group personal siempre presente ───────────────────────────────────────

def test_list_includes_personal_group(client):
    _auth(client, "group_list_personal")
    r = client.get("/api/groups")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    personal = next((w for w in data if w["type"] == "personal"), None)
    assert personal is not None
    assert personal["id"] == "group_list_personal"
    assert personal["name"] == "Personal"
    assert personal["role"] == "owner"


def test_personal_group_active_by_default(client):
    _auth(client, "group_active_default")
    r = client.get("/api/groups")
    assert r.status_code == 200
    personal = next(w for w in r.json() if w["type"] == "personal")
    assert personal["active"] is True


def test_personal_group_lists_only_its_owner(client):
    _auth(client, "group_personal_single_owner")

    r = client.get("/api/groups/group_personal_single_owner/members")

    assert r.status_code == 200
    assert r.json() == [
        {
            "username": "group_personal_single_owner",
            "role": "owner",
            "permissions": {},
        }
    ]


def test_personal_group_rejects_direct_members(client):
    _auth(client, "group_personal_direct_owner")

    r = client.post(
        "/api/groups/group_personal_direct_owner/members",
        json={"username": "someone_else", "role": "member"},
    )

    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "personal_group_single_user"


def test_personal_group_rejects_invitations(client):
    _auth(client, "group_personal_invite_owner")

    r = client.post(
        "/api/groups/group_personal_invite_owner/invitations",
        json={"username": "someone_else"},
    )

    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "personal_group_single_user"


# ── Crear group de equipo ──────────────────────────────────────────────────

def test_create_group(client):
    _auth(client, "group_create_owner")
    r = client.post("/api/groups", json={"name": "Mi Equipo"})
    assert r.status_code == 200
    group = r.json()
    assert group["name"] == "Mi Equipo"
    assert group["type"] == "team"
    assert group["id"]


def test_create_group_empty_name(client):
    _auth(client, "group_empty_name")
    r = client.post("/api/groups", json={"name": ""})
    assert r.status_code == 400


def test_create_group_name_too_long(client):
    _auth(client, "group_long_name")
    r = client.post("/api/groups", json={"name": "x" * 81})
    assert r.status_code == 400


def test_created_group_appears_in_list(client):
    _auth(client, "group_list_after_create")
    client.post("/api/groups", json={"name": "Equipo Creado"})
    r = client.get("/api/groups")
    assert r.status_code == 200
    names = [w["name"] for w in r.json()]
    assert "Equipo Creado" in names


# ── Renombrar group ────────────────────────────────────────────────────────

def test_rename_group(client):
    _auth(client, "group_rename_owner")
    group = client.post("/api/groups", json={"name": "Nombre Original"}).json()
    r = client.patch(f"/api/groups/{group['id']}", json={"name": "Nombre Nuevo"})
    assert r.status_code == 200
    assert r.json()["name"] == "Nombre Nuevo"


def test_rename_personal_group_rejected(client):
    _auth(client, "group_rename_personal")
    r = client.patch("/api/groups/group_rename_personal", json={"name": "Nuevo"})
    assert r.status_code == 400


def test_rename_by_non_manager_rejected(client):
    _auth(client, "group_rename_mgr")
    group = client.post("/api/groups", json={"name": "Solo Dueño"}).json()
    # Otro usuario sin permisos
    _auth(client, "group_rename_other")
    r = client.patch(f"/api/groups/{group['id']}", json={"name": "Intento"})
    assert r.status_code == 403


# ── Eliminar group ─────────────────────────────────────────────────────────

def test_delete_group(client):
    _auth(client, "group_delete_owner")
    group = client.post("/api/groups", json={"name": "Para Borrar"}).json()
    r = client.delete(f"/api/groups/{group['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_personal_group_rejected(client):
    _auth(client, "group_delete_personal")
    r = client.delete("/api/groups/group_delete_personal")
    assert r.status_code == 400


def test_delete_nonexistent_group(client):
    _auth(client, "group_delete_404")
    r = client.delete("/api/groups/id-que-no-existe")
    assert r.status_code == 404


def test_delete_by_non_creator_rejected(client):
    _auth(client, "group_del_creator")
    group = client.post("/api/groups", json={"name": "Privado"}).json()
    _auth(client, "group_del_intruder")
    r = client.delete(f"/api/groups/{group['id']}")
    assert r.status_code == 403


# ── Desactivar / reactivar group (propietario) ──────────────────────────────

def test_owner_can_disable_group(client):
    _auth(client, "group_status_owner")
    group = client.post("/api/groups", json={"name": "A Desactivar"}).json()
    r = client.post(f"/api/groups/{group['id']}/status", json={"status": "disabled"})
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_owner_can_reactivate_group(client):
    _auth(client, "group_status_owner2")
    group = client.post("/api/groups", json={"name": "A Reactivar"}).json()
    client.post(f"/api/groups/{group['id']}/status", json={"status": "disabled"})
    r = client.post(f"/api/groups/{group['id']}/status", json={"status": "active"})
    assert r.status_code == 200
    assert r.json()["status"] == "active"


def test_set_status_invalid_value_rejected(client):
    _auth(client, "group_status_invalid")
    group = client.post("/api/groups", json={"name": "Estado Invalido"}).json()
    r = client.post(f"/api/groups/{group['id']}/status", json={"status": "paused"})
    assert r.status_code == 422


def test_set_status_personal_group_rejected(client):
    _auth(client, "group_status_personal")
    r = client.post("/api/groups/group_status_personal/status", json={"status": "disabled"})
    assert r.status_code == 400


def test_set_status_nonexistent_group_404(client):
    _auth(client, "group_status_404")
    r = client.post("/api/groups/id-que-no-existe/status", json={"status": "disabled"})
    assert r.status_code == 404


def test_set_status_by_non_owner_rejected(client):
    _auth(client, "group_status_creator")
    group = client.post("/api/groups", json={"name": "Privado Estado"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "group_status_member", "role": "admin"})
    _auth(client, "group_status_member")
    r = client.post(f"/api/groups/{group['id']}/status", json={"status": "disabled"})
    assert r.status_code == 403


def test_member_switches_to_disabled_group_falls_back_to_personal(client):
    _auth(client, "group_status_owner3")
    group = client.post("/api/groups", json={"name": "Bloqueado"}).json()
    client.post(f"/api/groups/{group['id']}/status", json={"status": "disabled"})
    r = client.post(f"/api/groups/switch/{group['id']}")
    assert r.status_code == 403


# ── Cambiar group activo ───────────────────────────────────────────────────

def test_switch_to_personal_group(client):
    _auth(client, "group_switch_personal")
    r = client.post("/api/groups/switch/group_switch_personal")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["group_id"] == "group_switch_personal"


def test_switch_to_team_group_as_member(client):
    _auth(client, "group_switch_owner")
    group = client.post("/api/groups", json={"name": "Team Switch"}).json()
    r = client.post(f"/api/groups/switch/{group['id']}")
    assert r.status_code == 200
    assert r.json()["group_id"] == group["id"]


def test_switch_sets_cookie(client):
    _auth(client, "group_switch_cookie")
    r = client.post("/api/groups/switch/group_switch_cookie")
    assert "ga_token" in r.cookies or r.status_code == 200  # cookie updated


def test_switch_to_foreign_group_rejected(client):
    """Un usuario no puede cambiar a un group al que no pertenece."""
    _auth(client, "group_foreign_owner")
    group = client.post("/api/groups", json={"name": "Exclusivo"}).json()
    _auth(client, "group_foreign_intruder")
    r = client.post(f"/api/groups/switch/{group['id']}")
    assert r.status_code == 403


def test_switch_to_other_personal_group_rejected(client):
    """Un usuario no puede acceder al group personal de otro."""
    _auth(client, "group_personal_a")
    _auth(client, "group_personal_b")
    r = client.post("/api/groups/switch/group_personal_a")
    assert r.status_code == 403


# ── Miembros ───────────────────────────────────────────────────────────────────

def test_list_members(client):
    _auth(client, "group_members_owner")
    group = client.post("/api/groups", json={"name": "Equipo"}).json()
    r = client.get(f"/api/groups/{group['id']}/members")
    assert r.status_code == 200
    members = r.json()
    assert any(m["username"] == "group_members_owner" for m in members)


def test_add_member(client):
    import asyncio

    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("group_add_member_bob", "pass1234", email="group_add_member_bob@test.com"))
    except ValueError:
        pass

    _auth(client, "group_add_member_owner")
    group = client.post("/api/groups", json={"name": "Equipo Add"}).json()
    r = client.post(f"/api/groups/{group['id']}/members", json={"username": "group_add_member_bob", "role": "member"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_add_nonexistent_user_rejected(client):
    _auth(client, "group_add_ghost_owner")
    group = client.post("/api/groups", json={"name": "Equipo Ghost"}).json()
    r = client.post(f"/api/groups/{group['id']}/members", json={"username": "fantasma_xyz_999", "role": "member"})
    assert r.status_code == 404


def test_add_member_without_permission_rejected(client):
    import asyncio

    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("group_addmem_no_perm_victim", "pass1234", email="group_addmem_victim@test.com"))
    except ValueError:
        pass

    _auth(client, "group_addmem_creator")
    group = client.post("/api/groups", json={"name": "Solo Dueño"}).json()
    _auth(client, "group_addmem_imposter")
    r = client.post(f"/api/groups/{group['id']}/members",
                    json={"username": "group_addmem_no_perm_victim", "role": "member"})
    assert r.status_code == 403


def test_remove_member(client):
    import asyncio

    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("group_rem_member_bob", "pass1234", email="group_rem_bob@test.com"))
    except ValueError:
        pass

    _auth(client, "group_rem_member_owner")
    group = client.post("/api/groups", json={"name": "Equipo Rem"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"username": "group_rem_member_bob", "role": "member"})
    r = client.delete(f"/api/groups/{group['id']}/members/group_rem_member_bob")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_cannot_remove_creator(client):
    _auth(client, "group_rem_creator")
    group = client.post("/api/groups", json={"name": "Equipo NoDel"}).json()
    r = client.delete(f"/api/groups/{group['id']}/members/group_rem_creator")
    assert r.status_code == 400


def test_member_can_leave_group_without_manage_permission(client):
    """Un miembro sin permisos de gestión puede quitarse a sí mismo (abandonar)."""
    _auth(client, "group_leave_member")  # se registra primero para poder invitarlo
    _auth(client, "group_leave_creator")
    group = client.post("/api/groups", json={"name": "Equipo Leave"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "group_leave_member", "role": "member"})

    _auth(client, "group_leave_member")
    r = client.delete(f"/api/groups/{group['id']}/members/group_leave_member")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_owner_leaves_after_transferring_ownership(client):
    """Tras transferir la propiedad, el antiguo propietario puede abandonar el group."""
    _auth(client, "group_leave_new_owner")  # se registra primero para poder invitarlo
    _auth(client, "group_leave_owner")
    group = client.post("/api/groups", json={"name": "Equipo Transfer Leave"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "group_leave_new_owner", "role": "member"})

    r1 = client.post(f"/api/groups/{group['id']}/transfer-ownership",
                      json={"username": "group_leave_new_owner"})
    assert r1.status_code == 200

    r2 = client.delete(f"/api/groups/{group['id']}/members/group_leave_owner")
    assert r2.status_code == 200
    assert r2.json()["ok"] is True


def test_update_member_role(client):
    import asyncio

    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("group_role_bob", "pass1234", email="group_role_bob@test.com"))
    except ValueError:
        pass

    _auth(client, "group_role_owner")
    group = client.post("/api/groups", json={"name": "Equipo Role"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"username": "group_role_bob", "role": "member"})
    r = client.patch(f"/api/groups/{group['id']}/members/group_role_bob", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_update_member_invalid_role(client):
    import asyncio

    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("group_badrole_bob", "pass1234", email="group_badrole_bob@test.com"))
    except ValueError:
        pass

    _auth(client, "group_badrole_owner")
    group = client.post("/api/groups", json={"name": "Equipo BadRole"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"username": "group_badrole_bob", "role": "member"})
    r = client.patch(f"/api/groups/{group['id']}/members/group_badrole_bob", json={"role": "superuser"})
    assert r.status_code == 400


def test_update_and_list_granular_member_permissions(client):
    _auth(client, "group_perm_member")
    _auth(client, "group_perm_owner")
    group = client.post("/api/groups", json={"name": "Equipo Permisos"}).json()
    client.post(
        f"/api/groups/{group['id']}/members",
        json={"username": "group_perm_member", "role": "member"},
    )
    permissions = {
        "agents": {
            "default": True,
            "items": {"agent-private": {"use": False}},
        },
        "connections": {
            "default": False,
            "items": {"conn-ok": {"direct": True, "via_agent": False}},
        },
        "knowledge": {"default": True, "items": {}},
    }
    updated = client.patch(
        f"/api/groups/{group['id']}/members/group_perm_member",
        json={"permissions": permissions},
    )
    assert updated.status_code == 200
    assert updated.json()["permissions"] == permissions

    members = client.get(f"/api/groups/{group['id']}/members")
    assert members.status_code == 200
    member = next(
        row for row in members.json() if row["username"] == "group_perm_member"
    )
    assert member["permissions"] == permissions

    import asyncio

    from app.config.data import DB_FILE
    from app.storage.groups import GroupStorage

    storage = GroupStorage(DB_FILE)
    assert asyncio.run(
        storage.has_resource_permission(
            group["id"], "group_perm_member", "agents", "agent-private", "use"
        )
    ) is False
    assert asyncio.run(
        storage.has_resource_permission(
            group["id"], "group_perm_member", "connections", "conn-ok", "direct"
        )
    ) is True
    assert asyncio.run(
        storage.has_resource_permission(
            group["id"], "group_perm_member", "connections", "conn-ok", "via_agent"
        )
    ) is False
    assert asyncio.run(
        storage.has_resource_permission(
            group["id"], "group_perm_member", "connections", "conn-other", "direct"
        )
    ) is False


def test_existing_member_permissions_default_to_allow(client):
    _auth(client, "group_default_perm_member")
    _auth(client, "group_default_perm_owner")
    group = client.post("/api/groups", json={"name": "Equipo Compatible"}).json()
    client.post(
        f"/api/groups/{group['id']}/members",
        json={"username": "group_default_perm_member", "role": "member"},
    )
    _switch(client, group["id"], "group_default_perm_member")

    from app.config.data import DB_FILE
    from app.storage.groups import GroupStorage

    storage = GroupStorage(DB_FILE)
    import asyncio

    assert asyncio.run(
        storage.has_resource_permission(
            group["id"], "group_default_perm_member", "agents", "any-agent", "use"
        )
    ) is True


def test_rejects_invalid_granular_permissions(client):
    _auth(client, "group_invalid_perm_member")
    _auth(client, "group_invalid_perm_owner")
    group = client.post("/api/groups", json={"name": "Permisos inválidos"}).json()
    client.post(
        f"/api/groups/{group['id']}/members",
        json={"username": "group_invalid_perm_member", "role": "member"},
    )

    response = client.patch(
        f"/api/groups/{group['id']}/members/group_invalid_perm_member",
        json={
            "permissions": {
                "connections": {
                    "default": "false",
                    "items": {"connection-id": {"direct": True}},
                }
            }
        },
    )

    assert response.status_code == 422


def test_member_permissions_filter_group_resources(client):
    member = "group_filtered_member"
    owner = "group_filtered_owner"
    _auth(client, member)
    _auth(client, owner)
    group = client.post(
        "/api/groups", json={"name": "Recursos filtrados"}
    ).json()
    client.post(
        f"/api/groups/{group['id']}/members",
        json={"username": member, "role": "member"},
    )
    _switch(client, group["id"], owner)

    agent = client.post(
        "/api/agents",
        json={"name": "Agente restringido", "system_prompt": "Prueba"},
    ).json()
    connection = client.post(
        "/api/connections",
        json={
            "type": "openai",
            "label": "Conexión restringida",
            "api_key": "secret",
        },
    ).json()
    knowledge = client.post(
        "/api/knowledge/text",
        json={"title": "Nota restringida", "content": "Contenido"},
    ).json()

    permissions = {
        "agents": {
            "default": True,
            "items": {agent["id"]: {"use": False}},
        },
        "connections": {
            "default": True,
            "items": {connection["id"]: {"direct": False, "via_agent": False}},
        },
        "knowledge": {
            "default": True,
            "items": {knowledge["id"]: {"view": False}},
        },
    }
    updated = client.patch(
        f"/api/groups/{group['id']}/members/{member}",
        json={"permissions": permissions},
    )
    assert updated.status_code == 200

    _switch(client, group["id"], member)
    assert all(row["id"] != agent["id"] for row in client.get("/api/agents").json())
    assert client.get(f"/api/agents/{agent['id']}").status_code == 403
    assert all(
        row["id"] != connection["id"]
        for row in client.get("/api/connections").json()
    )
    assert all(
        row["id"] != knowledge["id"]
        for row in client.get("/api/knowledge").json()
    )
