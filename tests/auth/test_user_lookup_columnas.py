"""La lista de columnas de `user_lookup` frente a la tabla real (mejora #03).

`_get_user_by` y compañía dejaron de hacer `SELECT *` para no arrastrar el
avatar en base64 (hasta 13,3 MB) en cada petición autenticada. El riesgo de una
lista escrita a mano es que una columna nueva no se añada aquí y desaparezca en
silencio de todas las respuestas: este test la caza.
"""

from __future__ import annotations

from app.auth.auth import register_user
from app.auth.user_lookup import (
    _EXCLUIDAS,
    _USER_COLS,
    get_user_by_identity,
    get_user_by_login,
    get_user_by_username,
)
from app.storage.db import open_db


async def _columnas_reales() -> set[str]:
    async with open_db() as conn:
        rows = await conn.fetchall("PRAGMA table_info(users)")
    return {r[1] for r in rows}


async def test_la_lista_cubre_todas_las_columnas_menos_las_grandes(patch_data_dir):
    reales = await _columnas_reales()
    listadas = {c.strip() for c in _USER_COLS.split(",")}

    faltan = reales - listadas - set(_EXCLUIDAS)
    assert not faltan, (
        f"Columnas nuevas en `users` sin añadir a _USER_COLS: {sorted(faltan)}. "
        "Añádelas o inclúyelas en _EXCLUIDAS a propósito."
    )
    inventadas = listadas - reales
    assert not inventadas, f"_USER_COLS nombra columnas que no existen: {sorted(inventadas)}"


async def test_no_se_traen_avatar_ni_cv(patch_data_dir):
    await register_user("conavatar", "pass1234")
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET avatar = ?, cv = ? WHERE username = ?",
            ("x" * 1000, "y" * 1000, "conavatar"),
        )
        await conn.commit()

    for user in (
        await get_user_by_username("conavatar"),
        await get_user_by_identity("conavatar"),
        await get_user_by_login("conavatar"),
    ):
        assert user is not None
        assert "avatar" not in user
        assert "cv" not in user


async def test_sigue_trayendo_lo_que_necesita_el_login(patch_data_dir):
    await register_user("normalito", "pass1234")
    user = await get_user_by_login("normalito")

    assert user is not None
    # Los campos que leen login.py, dependencies.py y get_user_role.
    for campo in ("id", "username", "email", "password_hash", "role", "is_active"):
        assert campo in user, f"falta {campo}"
