"""Tests de auth: hash/verify, authenticate, register_user, roles, list/delete."""
from __future__ import annotations

import json

import pytest


def test_hash_and_verify():
    from app.auth.auth import hash_password, verify_password
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_authenticate_admin_correct(patch_data_dir):
    from app.auth.auth import authenticate
    assert authenticate("admin", "admin") is True


def test_authenticate_admin_wrong(patch_data_dir):
    from app.auth.auth import authenticate
    assert authenticate("admin", "wrongpass") is False


def test_authenticate_unknown_user(patch_data_dir):
    from app.auth.auth import authenticate
    assert authenticate("nobody", "anything") is False


def test_register_and_authenticate(patch_data_dir):
    from app.auth.auth import authenticate, register_user
    register_user("newuser", "mypassword123")
    assert authenticate("newuser", "mypassword123") is True
    assert authenticate("newuser", "bad") is False


def test_register_duplicate_username(patch_data_dir):
    from app.auth.auth import register_user
    register_user("dup_user", "pass1")
    with pytest.raises(ValueError, match="ya está en uso"):
        register_user("dup_user", "pass2")


def test_register_duplicate_email(patch_data_dir):
    from app.auth.auth import register_user
    register_user("user_a", "pass1", email="same@example.com")
    with pytest.raises(ValueError, match="correo"):
        register_user("user_b", "pass2", email="same@example.com")


def test_register_cannot_use_admin_username(patch_data_dir):
    from app.auth.auth import register_user
    with pytest.raises(ValueError, match="no disponible"):
        register_user("admin", "somepass")


def test_get_user_role_admin(patch_data_dir):
    from app.auth.auth import get_user_role
    assert get_user_role("admin") == "admin"


def test_get_user_role_standard(patch_data_dir):
    from app.auth.auth import get_user_role, register_user
    register_user("stduser", "pass1234")
    assert get_user_role("stduser") == "standard"


def test_list_users_no_password_hash(patch_data_dir):
    from app.auth.auth import list_users, register_user
    register_user("listed", "pass1234")
    users = list_users()
    for u in users:
        assert "password_hash" not in u


def test_delete_user(patch_data_dir):
    from app.auth.auth import authenticate, delete_user, register_user
    register_user("to_delete", "pass1234")
    assert delete_user("to_delete") is True
    assert authenticate("to_delete", "pass1234") is False


def test_delete_nonexistent_user(patch_data_dir):
    from app.auth.auth import delete_user
    assert delete_user("ghost_user") is False


def test_ensure_admin_password_hashed(patch_data_dir, tmp_data_dir):
    """Si admin_password_plain existe, ensure_admin_password_hashed lo migra a hash."""
    from app.auth.auth import ensure_admin_password_hashed, authenticate
    # Escribir settings con plain password
    settings = {"admin_username": "admin", "admin_password_plain": "plainpass", "jwt_secret": "test-secret"}
    (tmp_data_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    ensure_admin_password_hashed()
    # Ahora debe autenticar con el hash y haber eliminado el plain
    loaded = json.loads((tmp_data_dir / "settings.json").read_text())
    assert "admin_password_plain" not in loaded
    assert "admin_password_hash" in loaded
    assert authenticate("admin", "plainpass") is True
