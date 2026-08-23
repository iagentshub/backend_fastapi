"""Tests for POST /api/auth/login (username/email + password)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _register(client, email: str = "user@example.com", password: str = "pass1234"):
    username = email.split("@", 1)[0]
    r = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r


def test_login_success(client, reset_rate_limiter):
    _register(client, "login@example.com", "pass1234")
    r = client.post(
        "/api/auth/login", json={"email": "login@example.com", "password": "pass1234"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "username" in data
    assert "ga_token" in r.cookies


def test_login_success_with_username(client, reset_rate_limiter):
    _register(client, "publicuser@example.com", "pass1234")
    r = client.post(
        "/api/auth/login",
        json={"identifier": "publicuser", "password": "pass1234"},
    )
    assert r.status_code == 200
    assert r.json()["username"] == "publicuser"


def test_login_wrong_password(client, reset_rate_limiter):
    _register(client, "wrongpw@example.com", "pass1234")
    r = client.post(
        "/api/auth/login",
        json={"email": "wrongpw@example.com", "password": "wrongpass"},
    )
    assert r.status_code == 401


def test_login_wrong_password_pays_stored_bcrypt(client, reset_rate_limiter):
    from app.auth.passwords import DUMMY_PASSWORD_HASH

    _register(client, "storedhash@example.com", "pass1234")
    with patch(
        "app.api.routes.auth.session.verify_password_async", return_value=False
    ) as verify:
        r = client.post(
            "/api/auth/login",
            json={"email": "storedhash@example.com", "password": "wrongpass"},
        )

    assert r.status_code == 401
    verify.assert_awaited_once()
    attempted_password, stored_hash = verify.await_args.args
    assert attempted_password == "wrongpass"
    assert stored_hash != DUMMY_PASSWORD_HASH


def test_login_unknown_email(client):
    r = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "pass1234"}
    )
    assert r.status_code == 401


def test_login_unknown_email_pays_dummy_bcrypt(client):
    from app.auth.passwords import DUMMY_PASSWORD_HASH

    with patch(
        "app.api.routes.auth.session.verify_password_async", return_value=False
    ) as verify:
        r = client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "attempted-password"},
        )

    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_credentials"
    verify.assert_awaited_once_with("attempted-password", DUMMY_PASSWORD_HASH)


def test_login_account_without_local_password_pays_dummy_bcrypt(client):
    from app.auth.auth import register_user
    from app.auth.passwords import DUMMY_PASSWORD_HASH
    from app.storage.db import open_db

    async def _setup() -> None:
        await register_user("oauth_only", "temporary", email="oauth@example.com")
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = NULL WHERE username = ?",
                ("oauth_only",),
            )
            await conn.commit()

    asyncio.run(_setup())
    with patch(
        "app.api.routes.auth.session.verify_password_async", return_value=False
    ) as verify:
        r = client.post(
            "/api/auth/login",
            json={"email": "oauth@example.com", "password": "attempted-password"},
        )

    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_credentials"
    verify.assert_awaited_once_with("attempted-password", DUMMY_PASSWORD_HASH)


def test_login_missing_fields(client):
    r = client.post("/api/auth/login", json={"email": "someone@example.com"})
    assert r.status_code == 400


def test_login_sets_cookie(client, reset_rate_limiter):
    _register(client, "cookie@example.com", "pass1234")
    r = client.post(
        "/api/auth/login", json={"email": "cookie@example.com", "password": "pass1234"}
    )
    assert r.status_code == 200
    assert "ga_token" in r.cookies
