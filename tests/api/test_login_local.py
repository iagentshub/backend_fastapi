"""Tests de POST /api/auth/local (login de administrador)."""
from __future__ import annotations


def test_login_ok(client, reset_rate_limiter):
    r = client.post("/api/auth/local", json={"password": "admin"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["username"] == "admin"
    assert "ga_token" in r.cookies


def test_login_password_incorrecta(client, reset_rate_limiter):
    r = client.post("/api/auth/local", json={"password": "wrong"})
    assert r.status_code == 401


def test_login_password_vacia(client, reset_rate_limiter):
    r = client.post("/api/auth/local", json={"password": ""})
    assert r.status_code == 401


def test_login_rate_limit(client, reset_rate_limiter):
    """Tras 5 fallos consecutivos debe devolver 429."""
    for _ in range(5):
        client.post("/api/auth/local", json={"password": "bad"})
    r = client.post("/api/auth/local", json={"password": "bad"})
    assert r.status_code == 429


def test_logout_elimina_cookie(admin_client):
    r = admin_client.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json()["ok"] is True
