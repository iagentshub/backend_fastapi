"""Tests de POST /api/auth/login y POST /api/auth/logout."""
from __future__ import annotations


def test_login_ok(client, reset_rate_limiter):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["username"] == "admin"
    assert "ga_token" in r.cookies


def test_login_wrong_password(client, reset_rate_limiter):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user(client, reset_rate_limiter):
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert r.status_code == 401


def test_login_rate_limit(client, reset_rate_limiter):
    """Tras 5 fallos consecutivos debe devolver 429."""
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    r = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    assert r.status_code == 429


def test_logout_clears_cookie(admin_client):
    r = admin_client.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json()["ok"] is True
