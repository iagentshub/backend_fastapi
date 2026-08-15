"""Tests del logger contra PostgreSQL real.

La ruta PostgreSQL de `_DBHandler` no tenía ninguna cobertura: todos los tests
de `test_flog.py` usan SQLite. Eso era justo donde vivía la conexión psycopg2
fuera del pool, y donde asyncpg se comporta distinto (marcadores `$1`, tipos
estrictos, cierre asíncrono).

Se saltan enteros si no hay una base de datos de pruebas a mano. Para
ejecutarlos:

    docker run -d --rm --name flog-pgtest -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=flogtest -p 55432:5432 postgres:16-alpine
    GAIA_TEST_PG_DSN=postgresql://postgres:test@127.0.0.1:55432/flogtest \\
        pytest tests/utils/test_flog_postgres.py
"""

from __future__ import annotations

import asyncio
import logging
import os

import pytest

from app.utils.flog import _DBHandler

DSN = os.environ.get("GAIA_TEST_PG_DSN", "")

pytestmark = pytest.mark.skipif(
    not DSN, reason="define GAIA_TEST_PG_DSN para probar el logger contra PostgreSQL"
)


def _make_record(level: int, msg: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord("flog", level, "", 0, msg, (), None)
    for k, v in extra.items():
        setattr(record, k, v)
    return record


async def _reset() -> None:
    import asyncpg

    from app.storage.schema import SCHEMA_PG

    conn = await asyncpg.connect(DSN)
    try:
        for sentencia in SCHEMA_PG.split(";"):
            if "app_logs" in sentencia and sentencia.strip():
                await conn.execute(sentencia)
        await conn.execute("TRUNCATE app_logs")
    finally:
        await conn.close()


async def _leer() -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        filas = await conn.fetch(
            "SELECT ts, date, time, ip, username, level, source, summary "
            "FROM app_logs ORDER BY ts, id"
        )
        return [dict(f) for f in filas]
    finally:
        await conn.close()


@pytest.fixture()
def pg_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    asyncio.run(_reset())
    yield


@pytest.fixture()
def handler(pg_env):
    h = _DBHandler(None, batch_size=50, flush_interval=0)
    yield h
    h.close()


def test_no_importa_psycopg2(handler):
    """El motivo de la migración: psycopg2 ya no entra en el proceso."""
    import sys

    handler.emit(_make_record(logging.INFO, "x"))
    handler.flush()
    assert "psycopg2" not in sys.modules


def test_escribe_el_lote_completo(handler):
    for i in range(50):
        handler.emit(_make_record(logging.INFO, f"linea {i}", ip="10.0.0.1"))
    filas = asyncio.run(_leer())
    assert len(filas) == 50
    assert filas[0]["summary"] == "linea 0"
    assert filas[-1]["summary"] == "linea 49"


def test_tipos_correctos(handler):
    """asyncpg es estricto: ts es DOUBLE PRECISION y el resto TEXT."""
    handler.emit(
        _make_record(
            logging.WARNING, "tipos", ip="1.2.3.4", username="admin", source="FE"
        )
    )
    handler.flush()
    fila = asyncio.run(_leer())[0]
    assert isinstance(fila["ts"], float)
    assert fila["level"] == "WARNING"
    assert fila["ip"] == "1.2.3.4"
    assert fila["username"] == "admin"
    assert fila["source"] == "FE"
    assert isinstance(fila["date"], str) and isinstance(fila["time"], str)


def test_error_no_espera_al_lote(handler):
    handler.emit(_make_record(logging.INFO, "rutina"))
    assert asyncio.run(_leer()) == []
    handler.emit(_make_record(logging.ERROR, "explotó"))
    assert [f["summary"] for f in asyncio.run(_leer())] == ["rutina", "explotó"]


def test_reconecta_si_la_conexion_muere(handler):
    """Un PostgreSQL reiniciado no deja el logging muerto para siempre."""
    handler.emit(_make_record(logging.INFO, "antes"))
    handler.flush()

    # Cierra la conexión por debajo, como haría un reinicio del servidor.
    handler._run_pg(handler._conn.close(), timeout=5)

    handler.emit(_make_record(logging.INFO, "durante"))
    handler.flush()  # falla, reconecta y reintenta el lote entero
    handler.emit(_make_record(logging.INFO, "despues"))
    handler.flush()

    resumenes = [f["summary"] for f in asyncio.run(_leer())]
    assert "antes" in resumenes
    assert "despues" in resumenes
    # El reintento tras reconectar salva el lote: antes se perdía.
    assert "durante" in resumenes


def test_close_vuelca_lo_pendiente(pg_env):
    h = _DBHandler(None, batch_size=100, flush_interval=0)
    h.emit(_make_record(logging.INFO, "ultimo aliento"))
    h.close()
    assert [f["summary"] for f in asyncio.run(_leer())] == ["ultimo aliento"]


def test_close_para_el_loop_propio(pg_env):
    """El hilo del loop es daemon, pero cerrarlo bien evita fugas entre tests."""
    h = _DBHandler(None, batch_size=1, flush_interval=0)
    h.emit(_make_record(logging.INFO, "abre el loop"))
    hilo = h._loop_thread
    assert hilo is not None and hilo.is_alive()
    h.close()
    hilo.join(timeout=3)
    assert not hilo.is_alive()
    assert h._loop is None
