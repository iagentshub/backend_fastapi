"""Tests de integración de los endpoints GDPR:
GET  /api/auth/me/deletion-status
POST /api/auth/me/request-deletion
POST /api/auth/me/cancel-deletion
GET  /api/auth/me/export
POST /api/groups/{id}/transfer-ownership
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.auth.auth import create_token, register_user, schedule_user_deletion

# ── Fixture: parcha gdpr.DB_FILE con la BD de test ───────────────────────────

@pytest.fixture(autouse=True)
def patch_gdpr_db(patch_data_dir):
    pass  # gdpr.py uses open_db() — no per-module DB_FILE to patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth(client: TestClient, username: str, password: str = "pass1234") -> TestClient:
    import asyncio
    try:
        asyncio.run(register_user(username, password, email=f"{username}@test.com"))
    except ValueError:
        pass
    client.cookies.set("ga_token", create_token(username))
    return client


def _create_group_api(client: TestClient, name: str = "Equipo Test") -> dict:
    """Crea group a través de la API (usa la BD del TestClient)."""
    r = client.post("/api/groups", json={"name": name})
    assert r.status_code == 200, f"Error creando group: {r.text}"
    return r.json()


def _user_id(username: str) -> str:
    import asyncio

    from app.auth.auth import get_user_by_username
    user = asyncio.run(get_user_by_username(username))
    assert user is not None
    return str(user["id"])


def _add_member_api(client: TestClient, group_id: str, username: str) -> None:
    """Añade un miembro al group a través de la API."""
    r = client.post(f"/api/groups/{group_id}/invitations", json={"username": username})
    assert r.status_code == 200, f"Error invitando miembro: {r.text}"
    inv_id = r.json()["id"]
    # Aceptar con el cliente del miembro
    member_client = TestClient(client.app, raise_server_exceptions=True)
    member_client.cookies.set("ga_token", create_token(username))
    r2 = member_client.post(f"/api/groups/invitations/{inv_id}/accept")
    assert r2.status_code == 200, f"Error aceptando invitación: {r2.text}"


# ── GET /api/auth/me/deletion-status ─────────────────────────────────────────

def test_deletion_status_require_auth(client):
    r = client.get("/api/auth/me/deletion-status")
    assert r.status_code == 401


def test_deletion_status_sin_solicitud(client):
    _auth(client, "ds_clean")
    r = client.get("/api/auth/me/deletion-status")
    assert r.status_code == 200
    data = r.json()
    assert data["scheduled"] is False
    assert data["deletion_date"] is None


def test_deletion_status_con_solicitud(client):
    _auth(client, "ds_scheduled")
    client.post("/api/auth/me/request-deletion")
    r = client.get("/api/auth/me/deletion-status")
    assert r.status_code == 200
    data = r.json()
    assert data["scheduled"] is True
    assert data["deletion_date"] is not None


# ── POST /api/auth/me/request-deletion ───────────────────────────────────────

def test_request_deletion_require_auth(client):
    r = client.post("/api/auth/me/request-deletion")
    assert r.status_code == 401


def test_request_deletion_ok(client):
    _auth(client, "rd_ok")
    r = client.post("/api/auth/me/request-deletion")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_request_deletion_409_si_es_owner_de_group(client):
    _auth(client, "rd_group_owner")
    # Crear group directamente en la misma BD que usa get_owned_groups
    import asyncio

    from app.storage.groups import GroupStorage
    asyncio.run(GroupStorage().create("Group Bloqueante", created_by=_user_id("rd_group_owner")))
    r = client.post("/api/auth/me/request-deletion")
    assert r.status_code == 409
    data = r.json()
    assert "groups" in data["detail"]
    assert len(data["detail"]["groups"]) == 1


def test_request_deletion_ok_si_groups_son_solo_miembro(client):
    import asyncio
    asyncio.run(register_user("rd_other_owner", "pass1234", email="rd_other@test.com"))
    other = TestClient(client.app, raise_server_exceptions=True)
    _auth(other, "rd_other_owner")
    group = _create_group_api(other, "Group Ajeno")

    asyncio.run(register_user("rd_member_only", "pass1234", email="rd_member@test.com"))
    other.post(f"/api/groups/{group['id']}/invitations", json={"username": "rd_member_only"})
    inv_id = other.get(f"/api/groups/{group['id']}/invitations").json()[0]["id"]

    _auth(client, "rd_member_only")
    client.post(f"/api/groups/invitations/{inv_id}/accept")
    r = client.post("/api/auth/me/request-deletion")
    assert r.status_code == 200


def test_request_deletion_idempotente(client):
    _auth(client, "rd_idem")
    client.post("/api/auth/me/request-deletion")
    r = client.post("/api/auth/me/request-deletion")
    assert r.status_code == 200


# ── POST /api/auth/me/cancel-deletion ────────────────────────────────────────

def test_cancel_deletion_token_invalido_sin_auth(client):
    # cancel-deletion no requiere sesión — valida solo el token
    r = client.post("/api/auth/me/cancel-deletion", json={"token": "token-inexistente"})
    assert r.status_code == 400


def test_cancel_deletion_ok(client):
    _auth(client, "cd_ok")
    import asyncio
    token = asyncio.run(schedule_user_deletion("cd_ok"))
    r = client.post("/api/auth/me/cancel-deletion", json={"token": token})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_cancel_deletion_estado_limpiado(client):
    _auth(client, "cd_clean")
    import asyncio
    token = asyncio.run(schedule_user_deletion("cd_clean"))
    client.post("/api/auth/me/cancel-deletion", json={"token": token})
    r = client.get("/api/auth/me/deletion-status")
    assert r.json()["scheduled"] is False


def test_cancel_deletion_token_invalido(client):
    _auth(client, "cd_invalid")
    r = client.post("/api/auth/me/cancel-deletion", json={"token": "token-falso"})
    assert r.status_code == 400


def test_cancel_deletion_sin_token(client):
    _auth(client, "cd_notoken")
    r = client.post("/api/auth/me/cancel-deletion", json={})
    assert r.status_code == 400


# ── GET /api/auth/me/export ───────────────────────────────────────────────────

def test_export_require_auth(client):
    r = client.get("/api/auth/me/export")
    assert r.status_code == 401


def test_export_devuelve_zip(client):
    _auth(client, "exp_api")
    r = client.get("/api/auth/me/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


def test_export_content_disposition(client):
    _auth(client, "exp_cd")
    r = client.get("/api/auth/me/export")
    assert "attachment" in r.headers.get("content-disposition", "")


def test_export_zip_valido(client):
    _auth(client, "exp_valid")
    r = client.get("/api/auth/me/export")
    assert zipfile.is_zipfile(io.BytesIO(r.content))


def test_export_zip_contiene_profile(client):
    _auth(client, "exp_profile")
    r = client.get("/api/auth/me/export")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "profile.json" in zf.namelist()


# ── POST /api/groups/{id}/transfer-ownership ─────────────────────────────

def _setup_transfer(client: TestClient, owner: str, member: str) -> str:
    """Crea group y añade member vía API. Devuelve group_id con cliente autenticado como owner."""
    import asyncio
    _auth(client, owner)
    group = _create_group_api(client, "Grupo Transferible")
    asyncio.run(register_user(member, "pass1234", email=f"{member}@test.com"))
    _add_member_api(client, group["id"], member)
    return group["id"]


def test_transfer_ownership_require_auth(client):
    import asyncio
    asyncio.run(register_user("transfer_anon_owner", "pass1234", email="tanon@test.com"))
    other = TestClient(client.app, raise_server_exceptions=True)
    _auth(other, "transfer_anon_owner")
    group = _create_group_api(other, "Grupo Anon")
    r = client.post(f"/api/groups/{group['id']}/transfer-ownership", json={"username": "nobody"})
    assert r.status_code == 401


def test_transfer_ownership_ok(client):
    group_id = _setup_transfer(client, "to_owner", "to_new_owner")
    r = client.post(f"/api/groups/{group_id}/transfer-ownership", json={"username": "to_new_owner"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_transfer_ownership_verifica_nuevo_owner(client):
    group_id = _setup_transfer(client, "to2_owner", "to2_member")
    client.post(f"/api/groups/{group_id}/transfer-ownership", json={"username": "to2_member"})
    r = client.get("/api/groups")
    # to2_member es ahora el owner; to2_owner ya no tiene ese group como "owned"
    group_list = r.json()
    team_groups = [w for w in group_list if w["type"] == "team" and w["id"] == group_id]
    assert len(team_groups) == 1


def test_transfer_ownership_nuevo_owner_no_miembro(client):
    group_id = _setup_transfer(client, "to3_owner", "to3_member")
    _auth(TestClient(client.app), "to3_outsider")
    _auth(client, "to3_owner")
    r = client.post(f"/api/groups/{group_id}/transfer-ownership", json={"username": "to3_outsider"})
    assert r.status_code == 400


def test_transfer_ownership_group_no_encontrado(client):
    _auth(client, "to4_owner")
    r = client.post("/api/groups/group-no-existe/transfer-ownership", json={"username": "anyone"})
    assert r.status_code == 404


def test_transfer_ownership_solo_el_owner_puede(client):
    group_id = _setup_transfer(client, "to5_owner", "to5_member")
    client.cookies.set("ga_token", create_token("to5_member"))
    r = client.post(f"/api/groups/{group_id}/transfer-ownership", json={"username": "to5_owner"})
    assert r.status_code == 403


def test_transfer_ownership_self(client):
    group_id = _setup_transfer(client, "to6_owner", "to6_member")
    r = client.post(f"/api/groups/{group_id}/transfer-ownership", json={"username": "to6_owner"})
    assert r.status_code == 400


def test_transfer_permite_eliminar_cuenta_tras_transferir(client):
    """Flujo completo: transferir group → solicitar borrado de cuenta."""
    group_id = _setup_transfer(client, "full_flow_owner", "full_flow_member")
    r = client.post(f"/api/groups/{group_id}/transfer-ownership", json={"username": "full_flow_member"})
    assert r.status_code == 200
    r = client.post("/api/auth/me/request-deletion")
    assert r.status_code == 200
