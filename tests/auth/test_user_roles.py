"""Tests de roles de usuario."""
from __future__ import annotations

from app.auth.auth import get_user_role, register_user


def test_rol_admin(patch_data_dir):
    assert get_user_role("admin") == "admin"


def test_rol_usuario_estandar(patch_data_dir):
    register_user("stduser", "pass1234")
    assert get_user_role("stduser") == "standard"


def test_rol_usuario_desconocido(patch_data_dir):
    assert get_user_role("nadie") == "standard"
