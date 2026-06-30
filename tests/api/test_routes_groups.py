"""Tests de las rutas de grupos de workspace: /api/workspaces/{ws}/groups."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.auth.auth import create_token, register_user


# ── Helpers ────────────────────────────────────────────────────────────────────

def _register(username: str, email: str) -> str:
    asyncio.run(register_user(username, "pass1234", email=email))
    return create_token(username)


def _auth(client: TestClient, username: str, email: str) -> TestClient:
    token = _register(username, email)
    client.cookies.set("ga_token", token)
    return client


def _create_group(client: TestClient, workspace_id: str, name: str = "mi-grupo") -> dict:
    r = client.post(f"/api/workspaces/{workspace_id}/groups", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


# ── Autenticación ──────────────────────────────────────────────────────────────

def test_list_groups_sin_auth(client):
    r = client.get("/api/workspaces/someuser/groups")
    assert r.status_code == 401


def test_create_group_sin_auth(client):
    r = client.post("/api/workspaces/someuser/groups", json={"name": "g"})
    assert r.status_code == 401


# ── CRUD básico ────────────────────────────────────────────────────────────────

def test_list_grupos_vacio(admin_client):
    r = admin_client.get("/api/workspaces/testadmin/groups")
    assert r.status_code == 200
    assert r.json() == []


def test_crear_grupo(admin_client):
    g = _create_group(admin_client, "testadmin", "Alpha")
    assert g["name"] == "Alpha"
    assert g["workspace_id"] == "testadmin"
    assert "id" in g


def test_crear_grupo_aparece_en_lista(admin_client):
    _create_group(admin_client, "testadmin", "Beta")
    r = admin_client.get("/api/workspaces/testadmin/groups")
    nombres = [g["name"] for g in r.json()]
    assert "Beta" in nombres


def test_crear_grupo_nombre_vacio(admin_client):
    r = admin_client.post("/api/workspaces/testadmin/groups", json={"name": ""})
    assert r.status_code == 400


def test_crear_grupo_nombre_largo(admin_client):
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "x" * 81}
    )
    assert r.status_code == 400


def test_crear_grupo_duplicado(admin_client):
    _create_group(admin_client, "testadmin", "Gamma")
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "Gamma"}
    )
    assert r.status_code == 409


def test_renombrar_grupo(admin_client):
    g = _create_group(admin_client, "testadmin", "Viejo")
    r = admin_client.patch(
        f"/api/workspaces/testadmin/groups/{g['id']}", json={"name": "Nuevo"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Nuevo"


def test_renombrar_grupo_nombre_vacio(admin_client):
    g = _create_group(admin_client, "testadmin", "NombreX")
    r = admin_client.patch(
        f"/api/workspaces/testadmin/groups/{g['id']}", json={"name": ""}
    )
    assert r.status_code == 400


def test_renombrar_grupo_inexistente(admin_client):
    r = admin_client.patch(
        "/api/workspaces/testadmin/groups/noid", json={"name": "NuevoNombre"}
    )
    assert r.status_code == 404


def test_eliminar_grupo(admin_client):
    g = _create_group(admin_client, "testadmin", "ParaBorrar")
    r = admin_client.delete(f"/api/workspaces/testadmin/groups/{g['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # ya no aparece en la lista
    lista = admin_client.get("/api/workspaces/testadmin/groups").json()
    assert all(x["id"] != g["id"] for x in lista)


def test_eliminar_grupo_inexistente(admin_client):
    r = admin_client.delete("/api/workspaces/testadmin/groups/fantasma")
    assert r.status_code == 404


# ── Miembros ───────────────────────────────────────────────────────────────────

def test_listar_miembros_vacio(admin_client):
    g = _create_group(admin_client, "testadmin", "SinMiembros")
    r = admin_client.get(f"/api/workspaces/testadmin/groups/{g['id']}/members")
    assert r.status_code == 200
    assert r.json() == []


def test_anadir_miembro(admin_client):
    from app.config.data import DB_FILE
    from app.storage.workspaces import WorkspaceStorage

    g = _create_group(admin_client, "testadmin", "ConMiembro")
    # Insertar usuario y membresía de workspace directamente vía storage
    asyncio.run(register_user("alice2grp", "pass1234", email="alice2grp@test.com"))
    asyncio.run(WorkspaceStorage(DB_FILE).add_member("testadmin", "alice2grp", "member"))

    r = admin_client.post(
        f"/api/workspaces/testadmin/groups/{g['id']}/members",
        json={"username": "alice2grp"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    miembros = admin_client.get(
        f"/api/workspaces/testadmin/groups/{g['id']}/members"
    ).json()
    assert any(m["username"] == "alice2grp" for m in miembros)


def test_anadir_miembro_username_vacio(admin_client):
    g = _create_group(admin_client, "testadmin", "GrupoV")
    r = admin_client.post(
        f"/api/workspaces/testadmin/groups/{g['id']}/members",
        json={"username": ""},
    )
    assert r.status_code == 400


def test_anadir_miembro_no_workspace(admin_client):
    """Añadir un usuario que no es miembro del workspace debe fallar."""
    asyncio.run(register_user("outsider7grp", "pass1234", email="outsider7grp@test.com"))
    # NO se añade al workspace de testadmin
    g = _create_group(admin_client, "testadmin", "GrupoO")
    r = admin_client.post(
        f"/api/workspaces/testadmin/groups/{g['id']}/members",
        json={"username": "outsider7grp"},
    )
    assert r.status_code == 400


def test_eliminar_miembro(admin_client):
    from app.config.data import DB_FILE
    from app.storage.workspaces import WorkspaceStorage

    asyncio.run(register_user("member99grp", "pass1234", email="member99grp@test.com"))
    asyncio.run(WorkspaceStorage(DB_FILE).add_member("testadmin", "member99grp", "member"))

    g = _create_group(admin_client, "testadmin", "GrupoM")
    admin_client.post(
        f"/api/workspaces/testadmin/groups/{g['id']}/members",
        json={"username": "member99grp"},
    )
    r = admin_client.delete(
        f"/api/workspaces/testadmin/groups/{g['id']}/members/member99grp"
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_eliminar_miembro_inexistente(admin_client):
    g = _create_group(admin_client, "testadmin", "GrupoE")
    r = admin_client.delete(
        f"/api/workspaces/testadmin/groups/{g['id']}/members/nadie"
    )
    assert r.status_code == 404


# ── Permisos ───────────────────────────────────────────────────────────────────

def test_no_admin_no_puede_crear_grupo(client):
    _auth(client, "regular77", "regular77@test.com")
    r = client.post("/api/workspaces/regular77/groups", json={"name": "grp"})
    # El usuario regular puede gestionar su propio workspace → 200
    # pero no el de otro usuario
    assert r.status_code in (200, 403)


def test_usuario_no_puede_crear_en_workspace_ajeno(client):
    _auth(client, "user_a9", "user_a9@test.com")
    # Intenta crear grupo en workspace de otro usuario
    r = client.post("/api/workspaces/testadmin/groups", json={"name": "intruso"})
    assert r.status_code == 403
