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
