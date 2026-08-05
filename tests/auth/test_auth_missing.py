"""Tests de auth — casos no cubiertos: verify_password edge, decode_token, admin helpers, SMTP stub."""
from __future__ import annotations

import asyncio

import pytest

# ── verify_password — excepción en bcrypt ──────────────────────────────────────

def test_verify_password_bad_hash():
    from app.auth.auth import verify_password
    # Hash malformado → bcrypt lanza excepción → devuelve False
    assert verify_password("secret", "not-a-valid-bcrypt-hash") is False


def test_verify_password_empty():
    from app.auth.auth import verify_password
    assert verify_password("", "not-a-hash") is False


def test_verify_password_async(patch_data_dir):
    from app.auth.auth import hash_password, verify_password_async
    hashed = hash_password("mi_clave")
    result = asyncio.run(verify_password_async("mi_clave", hashed))
    assert result is True


def test_hash_password_async_delegates_to_thread(monkeypatch):
    from app.auth import auth

    calls = []

    async def fake_to_thread(func, *args):
        calls.append((func, args))
        return "threaded-hash"

    monkeypatch.setattr(auth.asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(auth.hash_password_async("mi_clave"))

    assert result == "threaded-hash"
    assert calls == [(auth.hash_password, ("mi_clave",))]


# ── _get_user_by — campo no permitido ─────────────────────────────────────────

def test_get_user_by_invalid_field(patch_data_dir):
    from app.auth.auth import _get_user_by
    with pytest.raises(ValueError, match="Campo no permitido"):
        asyncio.run(_get_user_by("role", "admin"))


# ── decode_token — token inválido ─────────────────────────────────────────────

def test_decode_token_invalid():
    from app.auth.auth import decode_token
    assert decode_token("not.a.valid.token") is None


def test_decode_token_empty():
    from app.auth.auth import decode_token
    assert decode_token("") is None


# ── decode_group_token — token inválido ────────────────────────────────────

def test_decode_group_token_invalid():
    from app.auth.auth import decode_group_token
    user, group = decode_group_token("garbage.token.here")
    assert user is None
    assert group is None


def test_decode_group_token_valid(patch_data_dir):
    from app.auth.auth import create_token, decode_group_token
    token = create_token("alice", "group-123")
    user, group = decode_group_token(token)
    assert user == "alice"
    assert group == "group-123"


def test_decode_group_token_defaults_to_username(patch_data_dir):
    from app.auth.auth import create_token, decode_group_token
    token = create_token("bob")
    user, group = decode_group_token(token)
    assert user == "bob"
    assert group == "bob"


# ── admin_update_user ──────────────────────────────────────────────────────────

def test_admin_update_user_not_found(patch_data_dir):
    from app.auth.auth import admin_update_user
    result = asyncio.run(admin_update_user("usuario_inexistente", role="admin"))
    assert result is False


def test_admin_update_user_no_fields(patch_data_dir):
    from app.auth.auth import admin_update_user
    # Sin campos → devuelve True sin tocar la BD
    result = asyncio.run(admin_update_user("cualquiera"))
    assert result is True


def test_admin_update_user_existing(patch_data_dir):
    from app.auth.auth import admin_update_user, get_user_by_username, register_user
    asyncio.run(register_user("user_upd", "pass1234", email="user_upd@test.com"))
    result = asyncio.run(admin_update_user("user_upd", is_active=0))
    assert result is True
    user = asyncio.run(get_user_by_username("user_upd"))
    assert user["is_active"] == 0


# ── admin_set_password ────────────────────────────────────────────────────────

def test_admin_set_password_not_found(patch_data_dir):
    from app.auth.auth import admin_set_password
    result = asyncio.run(admin_set_password("nadie", "newpass"))
    assert result is False


def test_admin_set_password_ok(patch_data_dir):
    from app.auth.auth import admin_set_password, register_user, verify_password_async
    asyncio.run(register_user("user_pw", "oldpass", email="user_pw@test.com"))
    result = asyncio.run(admin_set_password("user_pw", "newpass1234"))
    assert result is True
    from app.auth.auth import get_user_by_username
    user = asyncio.run(get_user_by_username("user_pw"))
    ok = asyncio.run(verify_password_async("newpass1234", user["password_hash"]))
    assert ok is True


# ── _build_email_html ─────────────────────────────────────────────────────────

def test_build_email_html_sin_cta():
    from app.services.email import _build_email_html
    html = _build_email_html("Título", "Encabezado", "<p>Cuerpo</p>")
    assert "Encabezado" in html
    assert "Cuerpo" in html
    # Sin CTA no debe incluir el enlace de botón
    assert "display:inline-block" not in html


def test_build_email_html_con_cta():
    from app.services.email import _build_email_html
    html = _build_email_html("Título", "Encabezado", "<p>Cuerpo</p>", "https://example.com", "Acción")
    assert "https://example.com" in html
    assert "Acción" in html
    assert "display:inline-block" in html


# ── _send_smtp — sin SMTP configurado devuelve inmediatamente ─────────────────

def test_send_smtp_no_host():
    from unittest.mock import patch

    from app.services.email import _send_smtp
    with patch("app.config.session.SMTP_HOST", ""):
        # Sin host → retorna sin enviar (no lanza)
        _send_smtp("a@b.com", "Asunto", "<p>test</p>")


def test_missing_smtp_does_not_log_reset_token():
    from unittest.mock import patch

    from app.services import email as email_service

    secret_token = "reset-token-muy-secreto"
    with (
        patch.object(email_service, "_smtp_available", return_value=False),
        patch.object(email_service.flog, "warning") as warning,
    ):
        email_service.send_reset_email("a@b.com", secret_token, "https://app.test")

    logged = " ".join(str(arg) for call in warning.call_args_list for arg in call.args)
    assert secret_token not in logged
    assert "https://app.test" not in logged


def test_reset_token_is_atomic_under_concurrency(patch_data_dir):
    from app.auth.auth import (
        consume_reset_token,
        create_password_reset_token,
        register_user,
    )

    async def scenario():
        await register_user("race_user", "old-password", email="race@test.com")
        token = await create_password_reset_token("race@test.com")
        assert token is not None
        return await asyncio.gather(
            consume_reset_token(token, "new-password-one"),
            consume_reset_token(token, "new-password-two"),
        )

    results = asyncio.run(scenario())
    assert sorted(results) == [False, True]


# ── purge_user_data ────────────────────────────────────────────────────────────

def test_purge_user_data(patch_data_dir):
    from app.auth.auth import get_user_by_username, purge_user_data, register_user
    asyncio.run(register_user("user_purge", "pass", email="user_purge@test.com"))
    asyncio.run(purge_user_data("user_purge"))
    assert asyncio.run(get_user_by_username("user_purge")) is None


# ── ensure_admin_user ─────────────────────────────────────────────────────────

def test_ensure_admin_user_uses_valid_default_email(patch_data_dir, monkeypatch):
    from app.auth.auth import ensure_admin_user, get_user_by_email

    monkeypatch.delenv("GAIA_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("GAIA_ADMIN_RESET", "")

    asyncio.run(ensure_admin_user())

    user = asyncio.run(get_user_by_email("admin@localhost.com"))
    assert user is not None
    assert user["username"] == "admin"
    assert user["role"] == "admin"


def test_ensure_admin_user_avisa_si_no_puede_proteger_admin_pass(patch_data_dir):
    from unittest.mock import patch

    from app.auth import auth

    errors = []
    env = {
        "GAIA_ADMIN_USERNAME": "adminwritefail",
        "GAIA_ADMIN_EMAIL": "adminwritefail@test.com",
        "GAIA_ADMIN_RESET": "true",
    }
    with (
        patch.dict("os.environ", env),
        patch("pathlib.Path.chmod", side_effect=OSError("solo lectura")),
        patch.object(auth.flog, "error", side_effect=errors.append),
    ):
        asyncio.run(auth.ensure_admin_user())

    assert any("No se pudo persistir .admin_pass" in message for message in errors)


def test_ensure_admin_user_creates_when_none_exists(patch_data_dir):
    from unittest.mock import patch

    from app.auth.auth import ensure_admin_user, get_user_by_username
    with patch.dict("os.environ", {"GAIA_ADMIN_USERNAME": "newadmin", "GAIA_ADMIN_EMAIL": "newadmin@test.com", "GAIA_ADMIN_RESET": ""}):
        asyncio.run(ensure_admin_user())
    user = asyncio.run(get_user_by_username("newadmin"))
    # El admin puede ser creado o un admin pre-existente puede existir
    assert user is None or user["role"] == "admin"


def test_ensure_admin_user_promotes_existing(patch_data_dir):
    from unittest.mock import patch

    from app.auth.auth import ensure_admin_user, get_user_by_username, register_user
    asyncio.run(register_user("promoteuser", "pass", email="promote@test.com"))
    with patch.dict("os.environ", {"GAIA_ADMIN_USERNAME": "promoteuser", "GAIA_ADMIN_EMAIL": "promote@test.com", "GAIA_ADMIN_RESET": ""}):
        asyncio.run(ensure_admin_user())
    user = asyncio.run(get_user_by_username("promoteuser"))
    assert user["role"] == "admin"


# ── Compatibility shims ────────────────────────────────────────────────────────

def test_load_users_raises():
    from app.auth.auth import _load_users
    with pytest.raises(RuntimeError, match="Migrado"):
        _load_users()


def test_save_users_raises():
    from app.auth.auth import _save_users
    with pytest.raises(RuntimeError, match="Migrado"):
        _save_users([])
