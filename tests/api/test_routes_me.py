"""Tests de GET /api/auth/me y POST /api/auth/change-password."""
from __future__ import annotations


def test_me_authenticated(admin_client):
    r = admin_client.get("/api/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "admin"
    assert data["role"] == "admin"


def test_me_standard_user(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"username": "stduser", "password": "pass1234"})
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "standard"


def test_me_unauthenticated(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_change_password_ok(admin_client):
    r = admin_client.post("/api/auth/change-password", json={
        "current_password": "admin",
        "new_password": "newpass123",
    })
    assert r.status_code == 200
    # Restaurar para no afectar otros tests
    admin_client.post("/api/auth/change-password", json={
        "current_password": "newpass123",
        "new_password": "admin",
    })


def test_change_password_wrong_current(admin_client):
    r = admin_client.post("/api/auth/change-password", json={
        "current_password": "wrongpass",
        "new_password": "newpass123",
    })
    assert r.status_code == 401


def test_change_password_too_short(admin_client):
    r = admin_client.post("/api/auth/change-password", json={
        "current_password": "admin",
        "new_password": "ab",
    })
    assert r.status_code == 400
