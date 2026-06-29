"""Tests de roles de usuario."""
from __future__ import annotations

import app.config.data as cfg
from app.auth.auth import get_user_role, register_user
from app.storage.db import open_db


async def test_rol_admin(patch_data_dir):
    await register_user("adminuser", "pass1234")
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET role = ? WHERE username = ?",
            ("admin", "adminuser"),
        )
        await conn.commit()
    assert await get_user_role("adminuser") == "admin"


async def test_rol_usuario_estandar(patch_data_dir):
    await register_user("stduser", "pass1234")
    assert await get_user_role("stduser") == "standard"


async def test_rol_usuario_desconocido(patch_data_dir):
    assert await get_user_role("nadie") == "standard"
