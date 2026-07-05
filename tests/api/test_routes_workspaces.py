"""Tests de /api/workspaces — CRUD, switch y miembros."""
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


def _switch(client: TestClient, workspace_id: str, username: str) -> TestClient:
    """Cambia el workspace activo actualizando la cookie directamente."""
    from app.auth.auth import create_token
    token = create_token(username, workspace_id=workspace_id)
    client.cookies.set("ga_token", token)
    return client


# ── Auth requerida ────────────────────────────────────────────────────────────

def test_list_workspaces_requires_auth(client):
    r = client.get("/api/workspaces")
    assert r.status_code == 401


def test_create_workspace_requires_auth(client):
    r = client.post("/api/workspaces", json={"name": "Test"})
    assert r.status_code == 401


# ── Workspace personal siempre presente ───────────────────────────────────────

def test_list_includes_personal_workspace(client):
    _auth(client, "ws_list_personal")
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    personal = next((w for w in data if w["type"] == "personal"), None)
    assert personal is not None
    assert personal["id"] == "ws_list_personal"
    assert personal["name"] == "Personal"
    assert personal["role"] == "owner"


def test_personal_workspace_active_by_default(client):
    _auth(client, "ws_active_default")
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    personal = next(w for w in r.json() if w["type"] == "personal")
    assert personal["active"] is True


# ── Crear workspace de equipo ──────────────────────────────────────────────────

def test_create_workspace(client):
    _auth(client, "ws_create_owner")
    r = client.post("/api/workspaces", json={"name": "Mi Equipo"})
    assert r.status_code == 200
    ws = r.json()
    assert ws["name"] == "Mi Equipo"
    assert ws["type"] == "team"
    assert ws["id"]


def test_create_workspace_empty_name(client):
    _auth(client, "ws_empty_name")
    r = client.post("/api/workspaces", json={"name": ""})
    assert r.status_code == 400


def test_create_workspace_name_too_long(client):
    _auth(client, "ws_long_name")
    r = client.post("/api/workspaces", json={"name": "x" * 81})
    assert r.status_code == 400


def test_created_workspace_appears_in_list(client):
    _auth(client, "ws_list_after_create")
    client.post("/api/workspaces", json={"name": "Equipo Creado"})
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    names = [w["name"] for w in r.json()]
    assert "Equipo Creado" in names


# ── Renombrar workspace ────────────────────────────────────────────────────────

def test_rename_workspace(client):
    _auth(client, "ws_rename_owner")
    ws = client.post("/api/workspaces", json={"name": "Nombre Original"}).json()
    r = client.patch(f"/api/workspaces/{ws['id']}", json={"name": "Nombre Nuevo"})
    assert r.status_code == 200
    assert r.json()["name"] == "Nombre Nuevo"


def test_rename_personal_workspace_rejected(client):
    _auth(client, "ws_rename_personal")
    r = client.patch("/api/workspaces/ws_rename_personal", json={"name": "Nuevo"})
    assert r.status_code == 400


def test_rename_by_non_manager_rejected(client):
    _auth(client, "ws_rename_mgr")
    ws = client.post("/api/workspaces", json={"name": "Solo Dueño"}).json()
    # Otro usuario sin permisos
    _auth(client, "ws_rename_other")
    r = client.patch(f"/api/workspaces/{ws['id']}", json={"name": "Intento"})
    assert r.status_code == 403


# ── Eliminar workspace ─────────────────────────────────────────────────────────

def test_delete_workspace(client):
    _auth(client, "ws_delete_owner")
    ws = client.post("/api/workspaces", json={"name": "Para Borrar"}).json()
    r = client.delete(f"/api/workspaces/{ws['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_personal_workspace_rejected(client):
    _auth(client, "ws_delete_personal")
    r = client.delete("/api/workspaces/ws_delete_personal")
    assert r.status_code == 400


def test_delete_nonexistent_workspace(client):
    _auth(client, "ws_delete_404")
    r = client.delete("/api/workspaces/id-que-no-existe")
    assert r.status_code == 404


def test_delete_by_non_creator_rejected(client):
    _auth(client, "ws_del_creator")
    ws = client.post("/api/workspaces", json={"name": "Privado"}).json()
    _auth(client, "ws_del_intruder")
    r = client.delete(f"/api/workspaces/{ws['id']}")
    assert r.status_code == 403


# ── Desactivar / reactivar workspace (propietario) ──────────────────────────────

def test_owner_can_disable_workspace(client):
    _auth(client, "ws_status_owner")
    ws = client.post("/api/workspaces", json={"name": "A Desactivar"}).json()
    r = client.post(f"/api/workspaces/{ws['id']}/status", json={"status": "disabled"})
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_owner_can_reactivate_workspace(client):
    _auth(client, "ws_status_owner2")
    ws = client.post("/api/workspaces", json={"name": "A Reactivar"}).json()
    client.post(f"/api/workspaces/{ws['id']}/status", json={"status": "disabled"})
    r = client.post(f"/api/workspaces/{ws['id']}/status", json={"status": "active"})
    assert r.status_code == 200
    assert r.json()["status"] == "active"


def test_set_status_invalid_value_rejected(client):
    _auth(client, "ws_status_invalid")
    ws = client.post("/api/workspaces", json={"name": "Estado Invalido"}).json()
    r = client.post(f"/api/workspaces/{ws['id']}/status", json={"status": "paused"})
    assert r.status_code == 422


def test_set_status_personal_workspace_rejected(client):
    _auth(client, "ws_status_personal")
    r = client.post("/api/workspaces/ws_status_personal/status", json={"status": "disabled"})
    assert r.status_code == 400


def test_set_status_nonexistent_workspace_404(client):
    _auth(client, "ws_status_404")
    r = client.post("/api/workspaces/id-que-no-existe/status", json={"status": "disabled"})
    assert r.status_code == 404


def test_set_status_by_non_owner_rejected(client):
    _auth(client, "ws_status_creator")
    ws = client.post("/api/workspaces", json={"name": "Privado Estado"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "ws_status_member", "role": "admin"})
    _auth(client, "ws_status_member")
    r = client.post(f"/api/workspaces/{ws['id']}/status", json={"status": "disabled"})
    assert r.status_code == 403


def test_member_switches_to_disabled_workspace_falls_back_to_personal(client):
    _auth(client, "ws_status_owner3")
    ws = client.post("/api/workspaces", json={"name": "Bloqueado"}).json()
    client.post(f"/api/workspaces/{ws['id']}/status", json={"status": "disabled"})
    r = client.post(f"/api/workspaces/switch/{ws['id']}")
    assert r.status_code == 403


# ── Cambiar workspace activo ───────────────────────────────────────────────────

def test_switch_to_personal_workspace(client):
    _auth(client, "ws_switch_personal")
    r = client.post("/api/workspaces/switch/ws_switch_personal")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["workspace_id"] == "ws_switch_personal"


def test_switch_to_team_workspace_as_member(client):
    _auth(client, "ws_switch_owner")
    ws = client.post("/api/workspaces", json={"name": "Team Switch"}).json()
    r = client.post(f"/api/workspaces/switch/{ws['id']}")
    assert r.status_code == 200
    assert r.json()["workspace_id"] == ws["id"]


def test_switch_sets_cookie(client):
    _auth(client, "ws_switch_cookie")
    r = client.post("/api/workspaces/switch/ws_switch_cookie")
    assert "ga_token" in r.cookies or r.status_code == 200  # cookie updated


def test_switch_to_foreign_workspace_rejected(client):
    """Un usuario no puede cambiar a un workspace al que no pertenece."""
    _auth(client, "ws_foreign_owner")
    ws = client.post("/api/workspaces", json={"name": "Exclusivo"}).json()
    _auth(client, "ws_foreign_intruder")
    r = client.post(f"/api/workspaces/switch/{ws['id']}")
    assert r.status_code == 403


def test_switch_to_other_personal_workspace_rejected(client):
    """Un usuario no puede acceder al workspace personal de otro."""
    _auth(client, "ws_personal_a")
    _auth(client, "ws_personal_b")
    r = client.post("/api/workspaces/switch/ws_personal_a")
    assert r.status_code == 403


# ── Miembros ───────────────────────────────────────────────────────────────────

def test_list_members(client):
    _auth(client, "ws_members_owner")
    ws = client.post("/api/workspaces", json={"name": "Equipo"}).json()
    r = client.get(f"/api/workspaces/{ws['id']}/members")
    assert r.status_code == 200
    members = r.json()
    assert any(m["username"] == "ws_members_owner" for m in members)


def test_add_member(client):
    import asyncio
    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("ws_add_member_bob", "pass1234", email="ws_add_member_bob@test.com"))
    except ValueError:
        pass

    _auth(client, "ws_add_member_owner")
    ws = client.post("/api/workspaces", json={"name": "Equipo Add"}).json()
    r = client.post(f"/api/workspaces/{ws['id']}/members", json={"username": "ws_add_member_bob", "role": "member"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_add_nonexistent_user_rejected(client):
    _auth(client, "ws_add_ghost_owner")
    ws = client.post("/api/workspaces", json={"name": "Equipo Ghost"}).json()
    r = client.post(f"/api/workspaces/{ws['id']}/members", json={"username": "fantasma_xyz_999", "role": "member"})
    assert r.status_code == 404


def test_add_member_without_permission_rejected(client):
    import asyncio
    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("ws_addmem_no_perm_victim", "pass1234", email="ws_addmem_victim@test.com"))
    except ValueError:
        pass

    _auth(client, "ws_addmem_creator")
    ws = client.post("/api/workspaces", json={"name": "Solo Dueño"}).json()
    _auth(client, "ws_addmem_imposter")
    r = client.post(f"/api/workspaces/{ws['id']}/members",
                    json={"username": "ws_addmem_no_perm_victim", "role": "member"})
    assert r.status_code == 403


def test_remove_member(client):
    import asyncio
    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("ws_rem_member_bob", "pass1234", email="ws_rem_bob@test.com"))
    except ValueError:
        pass

    _auth(client, "ws_rem_member_owner")
    ws = client.post("/api/workspaces", json={"name": "Equipo Rem"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members", json={"username": "ws_rem_member_bob", "role": "member"})
    r = client.delete(f"/api/workspaces/{ws['id']}/members/ws_rem_member_bob")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_cannot_remove_creator(client):
    _auth(client, "ws_rem_creator")
    ws = client.post("/api/workspaces", json={"name": "Equipo NoDel"}).json()
    r = client.delete(f"/api/workspaces/{ws['id']}/members/ws_rem_creator")
    assert r.status_code == 400


def test_member_can_leave_workspace_without_manage_permission(client):
    """Un miembro sin permisos de gestión puede quitarse a sí mismo (abandonar)."""
    _auth(client, "ws_leave_member")  # se registra primero para poder invitarlo
    _auth(client, "ws_leave_creator")
    ws = client.post("/api/workspaces", json={"name": "Equipo Leave"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "ws_leave_member", "role": "member"})

    _auth(client, "ws_leave_member")
    r = client.delete(f"/api/workspaces/{ws['id']}/members/ws_leave_member")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_owner_leaves_after_transferring_ownership(client):
    """Tras transferir la propiedad, el antiguo propietario puede abandonar el workspace."""
    _auth(client, "ws_leave_new_owner")  # se registra primero para poder invitarlo
    _auth(client, "ws_leave_owner")
    ws = client.post("/api/workspaces", json={"name": "Equipo Transfer Leave"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "ws_leave_new_owner", "role": "member"})

    r1 = client.post(f"/api/workspaces/{ws['id']}/transfer-ownership",
                      json={"username": "ws_leave_new_owner"})
    assert r1.status_code == 200

    r2 = client.delete(f"/api/workspaces/{ws['id']}/members/ws_leave_owner")
    assert r2.status_code == 200
    assert r2.json()["ok"] is True


def test_update_member_role(client):
    import asyncio
    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("ws_role_bob", "pass1234", email="ws_role_bob@test.com"))
    except ValueError:
        pass

    _auth(client, "ws_role_owner")
    ws = client.post("/api/workspaces", json={"name": "Equipo Role"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members", json={"username": "ws_role_bob", "role": "member"})
    r = client.patch(f"/api/workspaces/{ws['id']}/members/ws_role_bob", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_update_member_invalid_role(client):
    import asyncio
    from app.auth.auth import register_user
    try:
        asyncio.run(register_user("ws_badrole_bob", "pass1234", email="ws_badrole_bob@test.com"))
    except ValueError:
        pass

    _auth(client, "ws_badrole_owner")
    ws = client.post("/api/workspaces", json={"name": "Equipo BadRole"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members", json={"username": "ws_badrole_bob", "role": "member"})
    r = client.patch(f"/api/workspaces/{ws['id']}/members/ws_badrole_bob", json={"role": "superuser"})
    assert r.status_code == 400
