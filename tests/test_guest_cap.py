"""El invitado es un usuario efímero: alta, tope y purga.

Antes esto probaba un dict de proceso. Lo que se comprueba ahora es lo que ese
dict no podía dar: que el alta escribe en la BD —la que comparten todos los
workers—, que el tope es el del clúster y no el del proceso, y que cerrar
sesión no deja nada detrás.
"""
from __future__ import annotations

import pytest

import app.storage.guest as guest_mod
from app.auth.gdpr import purge_expired_guests
from app.auth.user_lookup import get_user_by_identity
from app.storage.guest import create_guest_user, is_guest


@pytest.mark.asyncio
async def test_el_alta_crea_un_usuario_con_rol_guest(tmp_data_dir):
    guest_id = await create_guest_user()
    assert is_guest(guest_id)
    row = await get_user_by_identity(guest_id)
    assert row is not None, "el invitado no llegó a la BD"
    assert row["role"] == "guest"
    assert row["id"] == guest_id


@pytest.mark.asyncio
async def test_dos_invitados_son_usuarios_distintos(tmp_data_dir):
    primero = await create_guest_user()
    segundo = await create_guest_user()
    assert primero != segundo


@pytest.mark.asyncio
async def test_el_tope_responde_503(tmp_data_dir, monkeypatch):
    from app.errors import APIError

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 2)
    await create_guest_user()
    await create_guest_user()
    with pytest.raises(APIError) as exc:
        await create_guest_user()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_el_tope_cuenta_en_la_bd_no_en_el_proceso(tmp_data_dir, monkeypatch):
    """El fallo original: con varios workers el tope real era el declarado × N.

    Recargar el módulo simula el segundo worker —memoria nueva, misma BD—: si el
    contador siguiera en el proceso, aquí habría sitio para dos invitados más.
    """
    import importlib

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 2)
    await create_guest_user()
    await create_guest_user()

    otro_worker = importlib.reload(guest_mod)
    monkeypatch.setattr(otro_worker, "MAX_SESSIONS", 2)
    from app.errors import APIError

    with pytest.raises(APIError) as exc:
        await otro_worker.create_guest_user()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_la_purga_respeta_al_invitado_recien_creado(tmp_data_dir):
    """Sin margen de gracia, el alta se purgaría a sí misma: el usuario existe
    antes que su sesión."""
    guest_id = await create_guest_user()
    assert await purge_expired_guests() == 0
    assert await get_user_by_identity(guest_id) is not None


@pytest.mark.asyncio
async def test_la_purga_se_lleva_al_invitado_sin_sesion(tmp_data_dir, monkeypatch):
    import app.config.session as session_mod

    monkeypatch.setattr(session_mod, "GUEST_GRACE_SECONDS", 0)
    guest_id = await create_guest_user()
    assert await purge_expired_guests() == 1
    assert await get_user_by_identity(guest_id) is None


@pytest.mark.asyncio
async def test_al_topar_la_purga_libera_hueco(tmp_data_dir, monkeypatch):
    """El alta no purga en el camino feliz, así que el hueco lo hace al topar.

    Purgar en cada alta era lo evidente y costaba un borrado RGPD por invitado
    abandonado en la primera petición de la demo (608 ms medidos con 150).
    Ahora solo lo paga quien encuentra la puerta cerrada — pero tiene que
    seguir entrando, que es lo que comprueba esto: sin la purga, aquí habría un
    503 con la casa vacía.
    """
    import app.config.session as session_mod

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 2)
    monkeypatch.setattr(session_mod, "GUEST_GRACE_SECONDS", 3600)
    await create_guest_user()
    await create_guest_user()  # tope alcanzado, y ninguno purgable todavía

    monkeypatch.setattr(session_mod, "GUEST_GRACE_SECONDS", 0)  # ya son abandonados
    tercero = await create_guest_user()

    assert await get_user_by_identity(tercero) is not None


@pytest.mark.asyncio
async def test_altas_simultaneas_no_se_pasan_del_tope(tmp_data_dir, monkeypatch):
    """El punto 22 quitó el factor workers y dejó la misma carrera en pequeño.

    El COUNT y el INSERT viajaban por conexiones distintas, así que N altas
    simultáneas leían todas el mismo recuento y entraban todas: el tope se
    rebasaba en tantos como peticiones coincidieran en la ventana. Y el bloque
    que parecía comprobar junto al INSERT releía una variable de Python.

    Cada invitado de más no es solo una fila en `users`: arrastra todo lo que
    cree en su sesión, y el tope es la única pieza que dimensiona la demo.
    """
    import asyncio

    from app.errors import APIError
    from app.sql import sql
    from app.storage.db import open_db

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 3)

    async def intentar():
        try:
            return await create_guest_user()
        except APIError:
            return None

    resultados = await asyncio.gather(*(intentar() for _ in range(12)))

    async with open_db() as conn:
        activos = await conn.fetchval(sql("queries/guest:count_guests"))

    assert activos == 3, f"el tope se rebasó: {activos} invitados con MAX_SESSIONS=3"
    assert len([r for r in resultados if r]) == 3


@pytest.mark.asyncio
async def test_el_tope_a_cero_desactiva_el_invitado(tmp_data_dir, monkeypatch):
    """Desactivar el modo invitado no puede depender de la forma del chequeo."""
    from app.errors import APIError

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 0)
    with pytest.raises(APIError) as exc:
        await create_guest_user()
    assert exc.value.status_code == 503
