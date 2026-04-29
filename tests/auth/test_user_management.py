"""Tests de gestión de usuarios: registro, listado y borrado."""
from __future__ import annotations

import pytest

from app.auth.auth import delete_user, list_users, register_user


def test_registro_ok(patch_data_dir):
    register_user("newuser", "pass1234")
    users = list_users()
    assert any(u["username"] == "newuser" for u in users)


def test_registro_nombre_duplicado(patch_data_dir):
    register_user("dup_user", "pass1")
    with pytest.raises(ValueError, match="ya está en uso"):
        register_user("dup_user", "pass2")


def test_registro_email_duplicado(patch_data_dir):
    register_user("user_a", "pass1", email="same@example.com")
    with pytest.raises(ValueError, match="correo"):
        register_user("user_b", "pass2", email="same@example.com")


def test_registro_nombre_admin_permitido(patch_data_dir):
    register_user("admin", "somepass")
    assert any(u["username"] == "admin" for u in list_users())


def test_listado_sin_password_hash(patch_data_dir):
    register_user("listed", "pass1234")
    for u in list_users():
        assert "password_hash" not in u


def test_borrado_ok(patch_data_dir):
    register_user("to_delete", "pass1234")
    assert delete_user("to_delete") is True
    assert not any(u["username"] == "to_delete" for u in list_users())


def test_borrado_usuario_inexistente(patch_data_dir):
    assert delete_user("ghost_user") is False
