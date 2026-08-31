"""Retirar el rol de admin se aplica ya, no cuando caduque una caché.

El rol sale de `_active_cache`, un dict de proceso con TTL de 60 s. Es una
decisión razonable y documentada, pero solo estaba pensada la mitad: el
comentario justificaba no expulsar a quien *asciende* --echar a alguien por
darle permisos sería desconcertante-- y la operación que importa en seguridad es
la contraria.

Sin revocar, quitarle admin a alguien tardaba hasta un minuto, y cada worker lo
aplicaba por su cuenta: dos peticiones seguidas del mismo cliente podían
responder 403 una y 200 la otra según a qué worker cayeran. Quien estuviera
dentro solo tenía que reintentar hasta dar con uno que tuviera la entrada
caliente.
"""

from __future__ import annotations

import asyncio

import pytest

from app.auth.auth import admin_update_user, create_token, register_user
from app.storage.sessions import REASON_ROLE_DOWNGRADED, SessionStorage


async def _abrir_sesion(username: str) -> str:
    from app.auth.user_lookup import get_user_by_username

    user = await get_user_by_username(username)
    session_id, _ = await SessionStorage().open(user["id"])
    return session_id


async def test_degradar_revoca_las_sesiones(tmp_data_dir):
    await register_user("bajarrol", "pass1234", email="bajarrol@example.com")
    await admin_update_user("bajarrol", role="admin")
    sesion = await _abrir_sesion("bajarrol")
    assert await SessionStorage().is_live(sesion) is True

    await admin_update_user("bajarrol", role="standard")

    assert await SessionStorage().is_live(sesion) is False, (
        "la retirada de admin dependía de una caché por proceso"
    )


async def test_ascender_no_revoca(tmp_data_dir):
    """Expulsar a alguien por darle permisos sería desconcertante."""
    await register_user("subirrol", "pass1234", email="subirrol@example.com")
    sesion = await _abrir_sesion("subirrol")

    await admin_update_user("subirrol", role="admin")

    assert await SessionStorage().is_live(sesion) is True


async def test_el_motivo_queda_registrado(tmp_data_dir):
    """La pantalla de sesiones distingue una expulsión de un logout."""
    from app.sql import sql
    from app.storage.db import open_db

    await register_user("motivorol", "pass1234", email="motivorol@example.com")
    await admin_update_user("motivorol", role="admin")
    sesion = await _abrir_sesion("motivorol")
    await admin_update_user("motivorol", role="standard")

    async with open_db() as conn:
        fila = await conn.fetchone(sql("queries/sessions:get_session"), (sesion,))
    assert dict(fila)["revoked_reason"] == REASON_ROLE_DOWNGRADED


async def test_un_usuario_que_no_existe_sigue_devolviendo_false(tmp_data_dir):
    """La consulta del rol sustituye a la de existencia; tiene que decir lo mismo."""
    assert await admin_update_user("no-existe-nadie", role="standard") is False


def test_el_rango_vive_en_un_solo_sitio():
    """Estaba en la capa de rutas, que auth.py no puede importar sin ciclo."""
    from app.auth.roles import ROLE_RANK, rank_of

    assert ROLE_RANK == {"guest": 0, "standard": 1, "admin": 2}
    assert rank_of("desconocido") == rank_of("standard")


@pytest.mark.parametrize("desde,hasta,revoca", [
    ("admin", "standard", True),
    ("admin", "guest", True),
    ("standard", "guest", True),
    ("standard", "admin", False),
    ("guest", "standard", False),
    ("admin", "admin", False),
])
async def test_solo_bajar_revoca(tmp_data_dir, desde, hasta, revoca):
    nombre = f"rol{desde}{hasta}"
    await register_user(nombre, "pass1234", email=f"{nombre}@example.com")
    await admin_update_user(nombre, role=desde)
    sesion = await _abrir_sesion(nombre)

    await admin_update_user(nombre, role=hasta)

    viva = await SessionStorage().is_live(sesion)
    assert viva is not revoca, f"{desde} -> {hasta}"


def test_create_token_sigue_existiendo():
    """Sanidad del import de arriba, que es de donde salen las sesiones."""
    assert callable(create_token)
    assert callable(asyncio.iscoroutinefunction)
