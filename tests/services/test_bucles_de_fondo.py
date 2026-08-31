"""El bucle de mantenimiento de workflows tiene que sobrevivir a un fallo."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


async def test_el_bucle_sobrevive_a_un_fallo_de_base_de_datos():
    """Una ronda que revienta no puede llevarse la tarea por delante.

    Sin el `try`, un `database is locked` sacaba la excepción del `while True` y
    la ejecución huérfana se quedaba «en curso» para siempre, sin una línea en
    el log que lo dijera: la tupla de `_lifespan` mantiene viva la tarea, así
    que Python nunca llega a avisar de la excepción sin recoger.
    """
    from app.services import workflow_run_executor as mod

    fallos = AsyncMock(side_effect=[RuntimeError("database is locked"), None, None])
    with patch.object(mod, "WORKFLOW_TICK_SECONDS", 0.01),          patch.object(mod._storage, "fail_stale", fallos),          patch.object(mod, "flog") as log:
        tarea = asyncio.create_task(mod.workflow_run_maintenance_loop())
        for _ in range(200):
            if fallos.await_count >= 3:
                break
            await asyncio.sleep(0.01)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea

    # Siguió pasando después del fallo, y el fallo quedó registrado.
    assert fallos.await_count >= 3
    assert log.error.called


async def test_un_bucle_muerto_deja_constancia():
    """La guarda genérica: cubre también al bucle que alguien añada mañana."""
    from app.api.app import _avisar_si_muere

    async def revienta():
        raise RuntimeError("me muero")

    tarea = asyncio.create_task(revienta(), name="bucle-de-prueba")
    with patch("app.api.app.flog") as log:
        tarea.add_done_callback(_avisar_si_muere)
        with pytest.raises(RuntimeError):
            await tarea
        await asyncio.sleep(0)

    assert log.error.called
    assert "bucle-de-prueba" in log.error.call_args.args[0]


async def test_una_cancelacion_no_es_un_fallo():
    """El apagado cancela los cinco bucles: eso es lo normal, no una avería."""
    from app.api.app import _avisar_si_muere

    tarea = asyncio.create_task(asyncio.sleep(10), name="bucle-cancelado")
    with patch("app.api.app.flog") as log:
        tarea.add_done_callback(_avisar_si_muere)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        await asyncio.sleep(0)

    assert not log.error.called
    assert not log.warning.called
