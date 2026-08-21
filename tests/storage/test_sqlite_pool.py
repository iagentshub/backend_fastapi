"""Pool SQLite por worker: reutilización, limpieza y concurrencia real."""

from __future__ import annotations

import asyncio

import pytest

from app.config import database
from app.storage import db as db_mod
from app.storage.db import open_db


async def _reinit_pool(monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    monkeypatch.setenv("GAIA_SQLITE_POOL_SIZE", str(size))
    await db_mod.init_db(database.DB_FILE)


@pytest.mark.asyncio
async def test_reutiliza_tres_conexiones_sin_reabrir_por_adquisicion(monkeypatch):
    await _reinit_pool(monkeypatch, 3)

    used: list[int] = []
    for _ in range(12):
        async with open_db() as conn:
            used.append(id(conn._conn))
            assert await conn.fetchval("SELECT 1") == 1

    assert len(db_mod._sqlite_connections) == 3
    assert len(set(used)) == 3
    assert set(used) == {id(conn) for conn in db_mod._sqlite_connections}


@pytest.mark.asyncio
async def test_aplica_pragmas_de_sesion_a_cada_conexion(monkeypatch):
    await _reinit_pool(monkeypatch, 3)

    async def inspect() -> tuple[int, int]:
        async with open_db() as conn:
            return (
                await conn.fetchval("PRAGMA foreign_keys"),
                await conn.fetchval("PRAGMA busy_timeout"),
            )

    values = await asyncio.gather(*(inspect() for _ in range(3)))

    assert values == [(1, database.SQLITE_BUSY_TIMEOUT_MS)] * 3


@pytest.mark.asyncio
async def test_hace_rollback_antes_de_devolver_una_conexion(monkeypatch):
    await _reinit_pool(monkeypatch, 1)
    async with open_db() as conn:
        await conn.execute("CREATE TABLE pool_rollback(value TEXT)")
        await conn.commit()

    async with open_db() as conn:
        await conn.execute("INSERT INTO pool_rollback(value) VALUES (?)", ("leak",))
        assert conn._conn.in_transaction is True

    async with open_db() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM pool_rollback") == 0
        assert conn._conn.in_transaction is False


@pytest.mark.asyncio
async def test_pool_acotado_atiende_mas_tareas_que_conexiones(monkeypatch):
    await _reinit_pool(monkeypatch, 2)

    async def read_after_wait() -> int:
        async with open_db() as conn:
            await asyncio.sleep(0.01)
            assert await conn.fetchval("SELECT 1") == 1
            return id(conn._conn)

    used = await asyncio.wait_for(
        asyncio.gather(*(read_after_wait() for _ in range(12))), timeout=1
    )

    assert len(set(used)) == 2


@pytest.mark.asyncio
async def test_usuarios_concurrentes_conservan_sus_escrituras(monkeypatch):
    await _reinit_pool(monkeypatch, 3)
    async with open_db() as conn:
        await conn.execute("CREATE TABLE pool_users(owner TEXT PRIMARY KEY, value TEXT)")
        await conn.commit()

    async def write_for_user(index: int) -> None:
        async with open_db() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO pool_users(owner, value) VALUES (?, ?)",
                (f"user-{index}", f"value-{index}"),
            )
            # Mantiene brevemente el lock de escritura para ejercer busy_timeout
            # en las conexiones de los demás usuarios.
            await asyncio.sleep(0.005)

    await asyncio.wait_for(
        asyncio.gather(*(write_for_user(index) for index in range(12))), timeout=2
    )

    async with open_db() as conn:
        rows = await conn.fetchall("SELECT owner, value FROM pool_users ORDER BY owner")
    assert {(row["owner"], row["value"]) for row in rows} == {
        (f"user-{index}", f"value-{index}") for index in range(12)
    }


@pytest.mark.asyncio
async def test_cancelacion_devuelve_la_conexion_y_revierte(monkeypatch):
    await _reinit_pool(monkeypatch, 1)
    async with open_db() as conn:
        await conn.execute("CREATE TABLE pool_cancel(value TEXT)")
        await conn.commit()

    entered = asyncio.Event()

    async def cancelled_writer() -> None:
        async with open_db() as conn:
            await conn.execute("INSERT INTO pool_cancel(value) VALUES (?)", ("leak",))
            entered.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(cancelled_writer())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def count_rows() -> int:
        async with open_db() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM pool_cancel")

    assert await asyncio.wait_for(count_rows(), timeout=1) == 0


@pytest.mark.asyncio
async def test_cancelacion_durante_rollback_completa_la_limpieza(monkeypatch):
    await _reinit_pool(monkeypatch, 1)
    async with open_db() as conn:
        await conn.execute("CREATE TABLE pool_cancel_rollback(value TEXT)")
        await conn.commit()

    rollback_started = asyncio.Event()

    async def writer() -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO pool_cancel_rollback(value) VALUES (?)", ("leak",)
            )
            raw = conn._conn
            original_rollback = raw.rollback
            calls = 0

            async def interrupted_once() -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    rollback_started.set()
                    await asyncio.sleep(60)
                await original_rollback()

            raw.rollback = interrupted_once

    task = asyncio.create_task(writer())
    await rollback_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def count_rows() -> int:
        async with open_db() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM pool_cancel_rollback"
            )

    assert await asyncio.wait_for(count_rows(), timeout=1) == 0


@pytest.mark.asyncio
async def test_sustituye_una_conexion_cerrada_al_devolverla(monkeypatch):
    await _reinit_pool(monkeypatch, 1)
    original = db_mod._sqlite_connections[0]

    async with open_db() as conn:
        assert conn._conn is original
        await conn._conn.close()

    assert len(db_mod._sqlite_connections) == 1
    assert db_mod._sqlite_connections[0] is not original
    async with open_db() as conn:
        assert await conn.fetchval("SELECT 1") == 1


@pytest.mark.asyncio
async def test_cierre_es_idempotente_y_init_recrea_el_pool(monkeypatch):
    await _reinit_pool(monkeypatch, 2)
    originals = tuple(db_mod._sqlite_connections)

    await db_mod.close_db_pool()
    await db_mod.close_db_pool()

    assert db_mod._sqlite_pool is None
    assert db_mod._sqlite_connections == []

    await db_mod.init_db(database.DB_FILE)
    assert len(db_mod._sqlite_connections) == 2
    assert all(conn not in originals for conn in db_mod._sqlite_connections)
    async with open_db() as conn:
        assert await conn.fetchval("SELECT 1") == 1
