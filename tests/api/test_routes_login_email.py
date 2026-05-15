"""Tests for POST /api/auth/login (email+password)."""
from __future__ import annotations


def _register(client, email: str = "user@example.com", password: str = "pass1234"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


def test_login_success(client, reset_rate_limiter):
    _register(client, "login@example.com", "pass1234")
    r = client.post("/api/auth/login", json={"email": "login@example.com", "password": "pass1234"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "username" in data
    assert "ga_token" in r.cookies


def test_login_wrong_password(client, reset_rate_limiter):
    _register(client, "wrongpw@example.com", "pass1234")
    r = client.post("/api/auth/login", json={"email": "wrongpw@example.com", "password": "wrongpass"})
    assert r.status_code == 401


def test_login_unknown_email(client):
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "pass1234"})
    assert r.status_code == 401


def test_login_missing_fields(client):
    r = client.post("/api/auth/login", json={"email": "someone@example.com"})
    assert r.status_code == 400


def test_login_sets_cookie(client, reset_rate_limiter):
    _register(client, "cookie@example.com", "pass1234")
    r = client.post("/api/auth/login", json={"email": "cookie@example.com", "password": "pass1234"})
    assert r.status_code == 200
    assert "ga_token" in r.cookies
