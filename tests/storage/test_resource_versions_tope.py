"""El historial de versiones tiene tope, y el listado tiene página.

`resource_versions` guardaba una copia completa del recurso en cada guardado y
nada la borraba nunca: ni retención, ni tope, ni ajuste en el panel. El
disparador no es un evento externo sino el botón de guardar, así que la tabla
crecía en proporción a lo bien que trabajase el usuario.
"""

from __future__ import annotations

import pytest

from app.pagination.models import OffsetParams
from app.storage.resource_versions import ResourceVersionStorage


@pytest.fixture()
def historial():
    return ResourceVersionStorage()


async def _archivar(historial, cuantas: int, *, tipo="agent", rid="ag1", dueno="u1"):
    for i in range(cuantas):
        await historial.create(tipo, rid, dueno, {"name": f"v{i}"}, "u1")


async def test_el_tope_deja_solo_las_ultimas(historial, monkeypatch, tmp_data_dir):
    import app.storage.resource_versions as mod

    monkeypatch.setattr(mod, "MAX_VERSIONS_PER_RESOURCE", 5)
    await _archivar(historial, 12)

    page = await historial.list("agent", "ag1", "u1")
    assert page.total == 5, "el tope no se aplicó al archivar"
    # Las que quedan son las últimas, no las primeras.
    assert [v["version"] for v in page.items] == [12, 11, 10, 9, 8]


async def test_el_tope_no_toca_a_otro_recurso(historial, monkeypatch, tmp_data_dir):
    """La poda es por (tipo, id, dueño), no un barrido de la tabla."""
    import app.storage.resource_versions as mod

    monkeypatch.setattr(mod, "MAX_VERSIONS_PER_RESOURCE", 3)
    await _archivar(historial, 6, rid="ag1")
    await _archivar(historial, 2, rid="ag2")

    assert (await historial.list("agent", "ag1", "u1")).total == 3
    assert (await historial.list("agent", "ag2", "u1")).total == 2


async def test_el_listado_pagina(historial, tmp_data_dir):
    """Era la única ruta de listado del backend que se quedó sin paginar."""
    await _archivar(historial, 7)

    primera = await historial.list("agent", "ag1", "u1", page=OffsetParams(limit=3))
    assert [v["version"] for v in primera.items] == [7, 6, 5]
    assert primera.total == 7
    assert primera.has_more is True

    ultima = await historial.list(
        "agent", "ag1", "u1", page=OffsetParams(limit=3, offset=6)
    )
    assert [v["version"] for v in ultima.items] == [1]
    assert ultima.has_more is False
