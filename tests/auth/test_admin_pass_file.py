"""Ciclo de vida de .admin_pass (BE-05).

El fichero guarda en claro la contraseña temporal del admin para que el
instalador pueda mostrarla. Tenía dos problemas encadenados:

1. Solo se limpiaba desde el endpoint de cambio de contraseña del perfil —
   ni el token de recuperación ni el reseteo por admin lo tocaban— y aun ahí
   dependía de GAIA_DATA_DIR, que en instalaciones sin esa variable no existe.
2. Lo BORRABA, y ensure_admin_user() interpreta "fichero ausente" como
   instalación nueva: el siguiente arranque regeneraba la contraseña y tiraba
   la que el usuario acababa de elegir.
"""

from __future__ import annotations

import asyncio

# OJO: nada de "from app.config.data import DATA_DIR" aquí. Importarlo a nivel
# de módulo lo congela apuntando al directorio de colección de pytest y los
# asserts leerían un fichero distinto del que escribe el código (ver la nota de
# conftest.py). Se usa el fixture tmp_data_dir, que es el path real parcheado.


def _crear_admin(
    tmp_data_dir, email: str = "admin@localhost.com", username: str = "jefazo"
):
    from app.auth.auth import register_user
    from app.storage.db import open_db

    async def _setup():
        await register_user(username, "pass1234", email=email)
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET role = ? WHERE username = ?", ("admin", username)
            )
            await conn.commit()

    asyncio.run(_setup())
    (tmp_data_dir / ".admin_pass").write_text(
        "temporal-de-instalacion", encoding="utf-8"
    )
    return username


def test_cambiar_la_propia_contrasena_vacia_el_fichero(patch_data_dir, tmp_data_dir, monkeypatch):
    from app.auth.auth import set_own_password

    monkeypatch.setenv("GAIA_ADMIN_EMAIL", "admin@localhost.com")
    usuario = _crear_admin(tmp_data_dir)

    asyncio.run(set_own_password(usuario, "nueva-contrasena"))

    assert (tmp_data_dir / ".admin_pass").exists(), "borrarlo dispara un reseteo en el próximo arranque"
    assert (tmp_data_dir / ".admin_pass").read_text(encoding="utf-8") == ""


def test_el_reseteo_por_admin_tambien_lo_vacia(patch_data_dir, tmp_data_dir, monkeypatch):
    from app.auth.auth import admin_set_password

    monkeypatch.setenv("GAIA_ADMIN_EMAIL", "admin@localhost.com")
    usuario = _crear_admin(tmp_data_dir)

    assert asyncio.run(admin_set_password(usuario, "otra-contrasena")) is True
    assert (tmp_data_dir / ".admin_pass").read_text(encoding="utf-8") == ""


def test_el_token_de_recuperacion_tambien_lo_vacia(patch_data_dir, tmp_data_dir, monkeypatch):
    from app.auth.auth import consume_reset_token, create_password_reset_token

    monkeypatch.setenv("GAIA_ADMIN_EMAIL", "admin@localhost.com")
    _crear_admin(tmp_data_dir)

    token = asyncio.run(create_password_reset_token("admin@localhost.com"))
    assert token, "sin token no se puede probar este camino"
    assert asyncio.run(consume_reset_token(token, "recuperada-1234")) is True
    assert (tmp_data_dir / ".admin_pass").read_text(encoding="utf-8") == ""


def test_otro_usuario_no_toca_el_fichero_del_admin(patch_data_dir, tmp_data_dir, monkeypatch):
    from app.auth.auth import register_user, set_own_password

    monkeypatch.setenv("GAIA_ADMIN_EMAIL", "admin@localhost.com")
    _crear_admin(tmp_data_dir)
    asyncio.run(register_user("pepita", "pass1234", email="pepa@example.com"))

    asyncio.run(set_own_password("pepita", "otra-cosa-1234"))

    assert (tmp_data_dir / ".admin_pass").read_text(encoding="utf-8") == "temporal-de-instalacion"


def test_el_fichero_vacio_no_regenera_la_contrasena(patch_data_dir, tmp_data_dir, monkeypatch):
    """El caso que motivaba todo: tras cambiarla, reiniciar no debe tirarla."""
    from app.auth.auth import (
        ensure_admin_user,
        get_user_by_username,
        set_own_password,
        verify_password_async,
    )

    monkeypatch.setenv("GAIA_ADMIN_EMAIL", "admin@localhost.com")
    monkeypatch.setenv("GAIA_ADMIN_USERNAME", "jefazo")
    monkeypatch.setenv("GAIA_ADMIN_RESET", "")
    usuario = _crear_admin(tmp_data_dir)
    asyncio.run(set_own_password(usuario, "la-que-eligio-el-usuario"))

    asyncio.run(ensure_admin_user())  # simula el reinicio del backend

    fila = asyncio.run(get_user_by_username(usuario))
    assert asyncio.run(
        verify_password_async("la-que-eligio-el-usuario", fila["password_hash"])
    ), "ensure_admin_user regeneró la contraseña que el usuario acababa de elegir"
