"""Tests de POST /api/auth/register."""
from __future__ import annotations


def test_register_ok(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={
        "username": "newuser", "password": "pass1234", "email": "new@example.com"
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "ga_token" in r.cookies


def test_register_duplicate_username(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"username": "dup", "password": "pass1234"})
    r = client.post("/api/auth/register", json={"username": "dup", "password": "pass5678"})
    assert r.status_code == 409


def test_register_duplicate_email(client, reset_rate_limiter):
    client.post("/api/auth/register", json={
        "username": "user_a", "password": "pass1234", "email": "same@example.com"
    })
    r = client.post("/api/auth/register", json={
        "username": "user_b", "password": "pass5678", "email": "same@example.com"
    })
    assert r.status_code == 409


def test_register_invalid_username_chars(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"username": "bad name!", "password": "pass1234"})
    assert r.status_code == 400


def test_register_username_too_short(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"username": "ab", "password": "pass1234"})
    assert r.status_code == 400


def test_register_password_too_short(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"username": "validname", "password": "123"})
    assert r.status_code == 400


def test_register_cannot_use_admin_username(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"username": "admin", "password": "pass1234"})
    assert r.status_code == 409


def test_register_rate_limit(client, reset_rate_limiter):
    """Tras 5 registros desde la misma IP debe devolver 429."""
    for i in range(5):
        client.post("/api/auth/register", json={
            "username": f"ratelim{i}", "password": "pass1234"
        })
    r = client.post("/api/auth/register", json={"username": "ratelim5", "password": "pass1234"})
    assert r.status_code == 429
