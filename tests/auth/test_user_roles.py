"""Tests de roles de usuario."""
from __future__ import annotations

from app.auth.auth import get_user_role, register_user


def test_rol_admin(patch_data_dir):
    register_user("adminuser", "pass1234")
    import json as _json
    from app.config.data import SETTINGS_FILE
    users_path = SETTINGS_FILE.parent / "users.json"
    users = _json.loads(users_path.read_text())
    for u in users:
        if u["username"] == "adminuser":
            u["role"] = "admin"
    users_path.write_text(_json.dumps(users))
    assert get_user_role("adminuser") == "admin"


def test_rol_usuario_estandar(patch_data_dir):
    register_user("stduser", "pass1234")
    assert get_user_role("stduser") == "standard"


def test_rol_usuario_desconocido(patch_data_dir):
    assert get_user_role("nadie") == "standard"
