"""El modelo de principal es uno solo: rol + rango, sin comprobaciones sueltas."""
from __future__ import annotations

import pytest

from app.api.routes.auth.dependencies import _assert_min_role
from app.auth.auth import get_user_role
from app.errors import APIError
from app.storage.guest import new_guest_id


async def test_el_id_de_invitado_real_se_clasifica_como_guest(patch_data_dir):
    """Regresión del desajuste de prefijos.

    get_user_role tenía su propia comprobación (``"guest"`` / ``guest_``) que no
    encajaba con el ``guest:`` que emite new_guest_id(), así que todo invitado
    caía al default y salía como "standard". El id lo genera el generador real
    a propósito: si el prefijo vuelve a cambiar, esta prueba se entera.
    """
    assert await get_user_role(new_guest_id()) == "guest"


def test_el_rango_ordena_invitado_registrado_admin():
    _assert_min_role("guest", "guest")
    _assert_min_role("standard", "standard")
    _assert_min_role("admin", "admin")
    _assert_min_role("admin", "standard")  # el rango es acumulativo

    with pytest.raises(APIError):
        _assert_min_role("guest", "standard")
    with pytest.raises(APIError):
        _assert_min_role("standard", "admin")


def test_rol_desconocido_vale_como_registrado_pero_no_como_admin():
    """'gestor' existe en BD (admin.py:497) y nadie ramifica sobre él."""
    _assert_min_role("gestor", "standard")
    with pytest.raises(APIError):
        _assert_min_role("gestor", "admin")


def test_el_invitado_recibe_un_codigo_de_error_distinguible():
    """El cliente necesita saber que le falta cuenta, no que le falta permiso."""
    with pytest.raises(APIError) as exc:
        _assert_min_role("guest", "standard")
    assert exc.value.detail["code"] == "guest_forbidden"
    assert exc.value.status_code == 403
