"""Tests de GET /api/auth/me y POST /api/auth/change-password."""
from __future__ import annotations


def test_me_autenticado(admin_client):
    r = admin_client.get("/api/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "admin"
    assert data["role"] == "admin"


def test_me_usuario_estandar(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"username": "stduser", "password": "pass1234"})
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "standard"


def test_me_sin_autenticar(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_change_password_admin_rechazado(admin_client):
    """El admin no puede cambiar su contraseña por API — usa GAIA_ADMIN_PASSWORD."""
    r = admin_client.post("/api/auth/change-password", json={
        "current_password": "admin",
        "new_password": "newpass123",
    })
    assert r.status_code == 400


def test_change_password_usuario_local_ok(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"username": "localuser", "password": "pass1234"})
    r = client.post("/api/auth/change-password", json={
        "current_password": "pass1234",
        "new_password": "newpass5678",
    })
    assert r.status_code == 200


def test_change_password_password_actual_incorrecta(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"username": "localuser2", "password": "pass1234"})
    r = client.post("/api/auth/change-password", json={
        "current_password": "wrongpass",
        "new_password": "newpass5678",
    })
    assert r.status_code == 401


def test_change_password_demasiado_corta(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"username": "localuser3", "password": "pass1234"})
    r = client.post("/api/auth/change-password", json={
        "current_password": "pass1234",
        "new_password": "ab",
    })
    assert r.status_code == 400
