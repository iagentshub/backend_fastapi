"""Tests para las rutas de password reset y verificación de email en /api/auth/."""
from __future__ import annotations

import asyncio
import hashlib

# ── POST /api/auth/forgot-password ──────────────────────────────────────────


def test_forgot_password_invalid_email_format(client):
    r = client.post("/api/auth/forgot-password", json={"email": "no-es-email"})
    assert r.status_code == 400


def test_forgot_password_empty_email(client):
    r = client.post("/api/auth/forgot-password", json={"email": ""})
    assert r.status_code == 400


def test_forgot_password_missing_email_field(client):
    r = client.post("/api/auth/forgot-password", json={})
    assert r.status_code == 400


def test_forgot_password_unknown_email_still_returns_200(client):
    """No revela si el email existe — siempre responde 200."""
    r = client.post("/api/auth/forgot-password", json={"email": "nadie@notexist.com"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_forgot_password_known_email_returns_200(client):
    from app.auth.auth import register_user

    asyncio.run(register_user("fp_user", "pass1234", email="fp_user@test.com"))
    r = client.post("/api/auth/forgot-password", json={"email": "fp_user@test.com"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── POST /api/auth/reset-password ────────────────────────────────────────────


def test_reset_password_missing_token_returns_400(client):
    r = client.post("/api/auth/reset-password", json={"password": "newpass123"})
    assert r.status_code == 400


def test_reset_password_missing_password_returns_400(client):
    r = client.post("/api/auth/reset-password", json={"token": "abc123"})
    assert r.status_code == 400


def test_reset_password_too_short_returns_400(client):
    from app.auth.auth import create_password_reset_token, register_user

    asyncio.run(register_user("short_pw", "pass1234", email="short_pw@test.com"))
    token = asyncio.run(create_password_reset_token("short_pw@test.com"))
    r = client.post("/api/auth/reset-password", json={"token": token, "password": "abc"})
    assert r.status_code == 400


def test_reset_password_invalid_token_returns_400(client):
    r = client.post(
        "/api/auth/reset-password",
        json={"token": "token-invalido-xyz", "password": "nuevapass123"},
    )
    assert r.status_code == 400


def test_reset_password_valid_token_returns_ok(client):
    from app.auth.auth import create_password_reset_token, register_user

    asyncio.run(register_user("valid_reset_user", "pass1234", email="valid_reset@test.com"))
    token = asyncio.run(create_password_reset_token("valid_reset@test.com"))
    assert token is not None
    r = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "newpassword99"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_reset_token_consumed_after_use(client):
    """El token de reset solo puede usarse una vez."""
    from app.auth.auth import create_password_reset_token, register_user

    asyncio.run(register_user("once_user", "pass1234", email="once_user@test.com"))
    token = asyncio.run(create_password_reset_token("once_user@test.com"))
    client.post("/api/auth/reset-password", json={"token": token, "password": "passfirst1"})
    r = client.post("/api/auth/reset-password", json={"token": token, "password": "passsecond2"})
    assert r.status_code == 400


def test_full_reset_flow_login_with_new_password(client):
    """Flujo completo: solicitar → obtener token → resetear → login con nueva contraseña."""
    from app.auth.auth import create_password_reset_token, register_user

    email = "flow_reset@test.com"
    asyncio.run(register_user("flow_reset_user", "oldpass99", email=email))

    client.post("/api/auth/forgot-password", json={"email": email})

    token = asyncio.run(create_password_reset_token(email))
    assert token is not None

    r = client.post("/api/auth/reset-password", json={"token": token, "password": "newpass2025"})
    assert r.status_code == 200

    r = client.post("/api/auth/login", json={"email": email, "password": "newpass2025"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_old_password_rejected_after_reset(client):
    """Tras el reset, la contraseña antigua ya no es válida."""
    from app.auth.auth import create_password_reset_token, register_user

    email = "oldpw_reset@test.com"
    asyncio.run(register_user("oldpw_reset_user", "oldpassword1", email=email))
    token = asyncio.run(create_password_reset_token(email))
    client.post("/api/auth/reset-password", json={"token": token, "password": "brandnewpass1"})

    r = client.post("/api/auth/login", json={"email": email, "password": "oldpassword1"})
    assert r.status_code == 401


# ── GET /api/auth/verify ──────────────────────────────────────────────────────


def test_verify_email_invalid_token_returns_400(client):
    r = client.get("/api/auth/verify?token=tokenquenoexiste")
    assert r.status_code == 400


def test_verify_email_valid_token_returns_ok(client):
    """Inserta manualmente verification_token y lo consume via el endpoint."""
    import secrets

    from app.auth.auth import register_user
    from app.storage.db import open_db

    asyncio.run(register_user("verify_me_user", "pass1234", email="verify_me@test.com"))

    async def _inject_token() -> str:
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET is_verified = 0, verification_token = ? WHERE username = ?",
                (token_hash, "verify_me_user"),
            )
            await conn.commit()
        return raw

    raw_token = asyncio.run(_inject_token())
    r = client.get(f"/api/auth/verify?token={raw_token}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["username"] == "verify_me_user"


def test_verify_email_token_only_works_once(client):
    """Un token de verificación no puede reutilizarse."""
    import secrets

    from app.auth.auth import register_user
    from app.storage.db import open_db

    asyncio.run(register_user("verify_once_user", "pass1234", email="verify_once@test.com"))

    async def _inject_token() -> str:
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET is_verified = 0, verification_token = ? WHERE username = ?",
                (token_hash, "verify_once_user"),
            )
            await conn.commit()
        return raw

    raw_token = asyncio.run(_inject_token())
    r1 = client.get(f"/api/auth/verify?token={raw_token}")
    assert r1.status_code == 200
    r2 = client.get(f"/api/auth/verify?token={raw_token}")
    assert r2.status_code == 400
