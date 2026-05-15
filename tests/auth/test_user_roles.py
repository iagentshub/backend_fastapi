"""Tests de roles de usuario."""
from __future__ import annotations

import app.config.data as cfg
from app.auth.auth import get_user_role, register_user
from app.storage.db import PH, close_db, open_db


def test_rol_admin(patch_data_dir):
    register_user("adminuser", "pass1234")
    conn = open_db(cfg.DB_FILE)
    try:
        conn.cursor().execute(
            f"UPDATE users SET role = {PH} WHERE username = {PH}",
            ("admin", "adminuser"),
        )
        conn.commit()
    finally:
        close_db(conn)
    assert get_user_role("adminuser") == "admin"


def test_rol_usuario_estandar(patch_data_dir):
    register_user("stduser", "pass1234")
    assert get_user_role("stduser") == "standard"


def test_rol_usuario_desconocido(patch_data_dir):
    assert get_user_role("nadie") == "standard"
