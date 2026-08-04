"""Tests for POST /api/auth/register."""
from __future__ import annotations


def test_register_ok(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={
        "username": "newuser", "email": "newuser@example.com", "password": "pass1234"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["email"] == "newuser@example.com"
    assert "ga_token" in r.cookies


def test_register_with_profile_fields(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={
        "email": "profile@example.com",
        "username": "profileuser",
        "password": "pass1234",
        "birth_date": "1990-01-15",
        "gender": "female",
        "country": "ES",
        "phone": "+34 600 000 000",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_register_duplicate_email(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"username": "sameuser1", "email": "same@example.com", "password": "pass1234"})
    r = client.post("/api/auth/register", json={"username": "sameuser2", "email": "same@example.com", "password": "pass5678"})
    assert r.status_code == 409


def test_register_invalid_email(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"username": "invalidemail", "email": "not-an-email", "password": "pass1234"})
    assert r.status_code == 400


def test_register_missing_email(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"username": "missingemail", "password": "pass1234"})
    assert r.status_code == 400


def test_register_password_too_short(client, reset_rate_limiter):
    r = client.post("/api/auth/register", json={"username": "validuser", "email": "valid@example.com", "password": "short"})
    assert r.status_code == 400


def test_register_rate_limit(client, reset_rate_limiter):
    """After 5 registrations from the same IP it should return 429."""
    for i in range(5):
        client.post("/api/auth/register", json={
            "username": f"ratelim{i}", "email": f"ratelim{i}@example.com", "password": "pass1234"
        })
    r = client.post("/api/auth/register", json={"username": "ratelim5", "email": "ratelim5@example.com", "password": "pass1234"})
    assert r.status_code == 429


def test_registro_acota_los_campos_de_perfil(client):
    """Iban a la BD sin tope: con el registro abierto, 2 MB de "país" por cuenta."""
    r = client.post(
        "/api/auth/register",
        json={
            "username": "usuariolargo",
            "email": "largo@example.com",
            "password": "pass1234",
            "country": "x" * 121,
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["field"] == "country"


def test_registro_acepta_un_perfil_normal(client):
    r = client.post(
        "/api/auth/register",
        json={
            "username": "usuarionormal",
            "email": "normal@example.com",
            "password": "pass1234",
            "country": "España",
            "gender": "prefiero-no-decirlo",
        },
    )
    assert r.status_code == 200, r.text
