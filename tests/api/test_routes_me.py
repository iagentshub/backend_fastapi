"""Tests de GET /api/auth/me y POST /api/auth/change-password."""
from __future__ import annotations


def test_me_autenticado(admin_client):
    r = admin_client.get("/api/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "testadmin"
    assert data["role"] == "admin"


def test_me_usuario_estandar(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "stduser@example.com", "password": "pass1234"})
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "standard"


def test_me_sin_autenticar(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_change_password_usuario_local_ok(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "localuser@example.com", "password": "pass1234"})
    r = client.post("/api/auth/change-password", json={
        "current_password": "pass1234",
        "new_password": "newpass5678",
    })
    assert r.status_code == 200


def test_change_password_password_actual_incorrecta(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "localuser2@example.com", "password": "pass1234"})
    r = client.post("/api/auth/change-password", json={
        "current_password": "wrongpass",
        "new_password": "newpass5678",
    })
    assert r.status_code == 401


def test_change_password_demasiado_corta(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "localuser3@example.com", "password": "pass1234"})
    r = client.post("/api/auth/change-password", json={
        "current_password": "pass1234",
        "new_password": "ab",
    })
    assert r.status_code == 400
