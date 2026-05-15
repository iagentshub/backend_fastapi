"""Tests for POST /api/auth/register."""
from __future__ import annotations


def test_register_ok(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={
        "email": "newuser@example.com", "password": "pass1234"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["email"] == "newuser@example.com"
    assert "ga_token" in r.cookies


def test_register_with_profile_fields(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={
        "email": "profile@example.com",
        "password": "pass1234",
        "birth_date": "1990-01-15",
        "gender": "female",
        "country": "ES",
        "phone": "+34 600 000 000",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_register_duplicate_email(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "same@example.com", "password": "pass1234"})
    r = client.post("/api/auth/register", json={"email": "same@example.com", "password": "pass5678"})
    assert r.status_code == 409


def test_register_invalid_email(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"email": "not-an-email", "password": "pass1234"})
    assert r.status_code == 400


def test_register_missing_email(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"password": "pass1234"})
    assert r.status_code == 400


def test_register_password_too_short(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"email": "valid@example.com", "password": "short"})
    assert r.status_code == 400


def test_register_rate_limit(client, reset_rate_limiter):
    """After 5 registrations from the same IP it should return 429."""
    for i in range(5):
        client.post("/api/auth/register", json={
            "email": f"ratelim{i}@example.com", "password": "pass1234"
        })
    r = client.post("/api/auth/register", json={"email": "ratelim5@example.com", "password": "pass1234"})
    assert r.status_code == 429
