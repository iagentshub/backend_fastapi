"""Cada consulta del catálogo se prepara de verdad, en cada motor.

Los tests de `test_sql_en_ficheros.py` comprueban la forma: que el SQL está en
fichero, que el identificador resuelve, que la consulta dialectal va bajo su
rama. Ninguno prueba que la consulta sea **válida**: una columna con una errata
o un `JOIN` a una tabla que ya no existe pasan los tres.

Aquí se prepara cada sentencia contra una base con el esquema real. Preparar y
no ejecutar es lo que hace falta: valida sintaxis, tablas y columnas sin tocar
un solo dato.

El motor que importa es PostgreSQL —la suite entera corre en SQLite, así que
sus consultas son las únicas que nadie mira hasta el despliegue— y ese test se
salta entero si no hay una base a mano. Para ejecutarlo:

    docker run -d --rm --name sqltest-pg -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=sqltest -p 55433:5432 postgres:16-alpine
    GAIA_TEST_PG_DSN=postgresql://postgres:test@127.0.0.1:55433/sqltest \\
        python3.11 -m pytest tests/storage/test_sql_contra_motores.py
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pytest

from app.sql import SQL_DIR, secciones_de
from app.storage.schema import schema_for

DSN = os.environ.get("GAIA_TEST_PG_DSN", "")

# `dbstat` es una tabla virtual que solo existe si SQLite se compiló con
# SQLITE_ENABLE_DBSTAT_VTAB; el visor de tamaños ya cuenta con no tenerla.
SIN_PREPARAR = {"queries/admin_stats:sqlite_table_size"}


def _catalogo() -> list[tuple[str, str]]:
    return [
        (f"queries/{ruta.stem}:{seccion}", cuerpo)
        for ruta in sorted((SQL_DIR / "queries").glob("*.sql"))
        for seccion, cuerpo in secciones_de(f"queries/{ruta.stem}").items()
    ]


# `@nombre@` es la misma convención de marcador que usa el esquema para los
# tipos por dialecto (`@BOOL@`, `@SERIAL@`…), aquí para una lista `IN` cuya
# longitud solo se conoce en tiempo de ejecución: el llamador la sustituye por
# tantos `?` como elementos tenga. Preparar el cuerpo tal cual falla con
# `unrecognized token: "@"` y dejaba la consulta fuera de los dos motores, que
# es justo la que nadie mira en PostgreSQL hasta el despliegue. Con un solo
# marcador la sintaxis, las tablas y las columnas se validan igual.
_MARCADOR_LISTA = re.compile(r"@[a-z_]+@")


def _preparable(cuerpo: str) -> str:
    return _MARCADOR_LISTA.sub("?", cuerpo)


def _motor(identificador: str, cuerpo: str) -> str | None:
    """`sqlite`, `pg` o None si la consulta vale para los dos."""
    from tests.storage.test_sql_en_ficheros import SOLO_PG, SOLO_SQLITE

    sqlite = any(re.search(p, cuerpo, re.I | re.M) for p in SOLO_SQLITE)
    pg = any(re.search(p, cuerpo, re.M) for p in SOLO_PG)
    if sqlite and not pg:
        return "sqlite"
    if pg and not sqlite:
        return "pg"
    return None


def _base_sqlite(tmp_path: Path) -> sqlite3.Connection:
    """Esquema completo, incluidas las tablas que solo crean las migraciones."""
    import asyncio

    from app.storage.db import migrate_schema

    destino = tmp_path / "esquema.db"
    asyncio.run(migrate_schema(destino))
    return sqlite3.connect(destino)


def test_todas_las_consultas_preparan_en_sqlite(tmp_path):
    conn = _base_sqlite(tmp_path)
    rotas = []
    try:
        for identificador, cuerpo in _catalogo():
            if _motor(identificador, cuerpo) == "pg" or identificador in SIN_PREPARAR:
                continue
            # EXPLAIN devuelve el bytecode y no ejecuta el programa, así que los
            # parámetros pueden ir a None sin insertar ni borrar nada.
            sentencia = _preparable(cuerpo)
            try:
                conn.execute(
                    f"EXPLAIN {sentencia}", [None] * sentencia.count("?")
                )
            except sqlite3.Error as exc:
                rotas.append(f"{identificador}: {exc}")
    finally:
        conn.close()

    assert rotas == [], "Consultas que SQLite no acepta: " + "; ".join(rotas)


def test_el_esquema_de_sqlite_se_aplica_entero(tmp_path):
    conn = sqlite3.connect(tmp_path / "solo_esquema.db")
    try:
        conn.executescript(schema_for("sqlite"))
    finally:
        conn.close()


@pytest.mark.skipif(
    not DSN, reason="define GAIA_TEST_PG_DSN para preparar las consultas en PostgreSQL"
)
def test_todas_las_consultas_preparan_en_postgres():
    import asyncio

    import asyncpg

    from app.storage.db import AsyncConn
    from app.storage.migrations.postgres import run_postgres_migrations

    traducir = AsyncConn(None, True)._pg_sql

    async def _correr() -> list[str]:
        conn = await asyncpg.connect(DSN)
        rotas: list[str] = []
        try:
            # Esquema base + migraciones: varias tablas (resource_social,
            # resource_labels, user_follows…) y columnas nacen en la secuencia
            # de migraciones, no en el DDL, y sin ellas medio catálogo no
            # resuelve.
            for sentencia in schema_for("pg").split(";"):
                if sentencia.strip():
                    await conn.execute(sentencia)
            await run_postgres_migrations(conn)
            for identificador, cuerpo in _catalogo():
                if _motor(identificador, cuerpo) == "sqlite":
                    continue
                try:
                    # prepare() valida sintaxis, tablas y columnas contra el
                    # esquema real sin ejecutar la consulta.
                    await conn.prepare(traducir(_preparable(cuerpo)))
                except asyncpg.PostgresError as exc:
                    rotas.append(f"{identificador}: {exc}")
        finally:
            await conn.close()
        return rotas

    rotas = asyncio.run(_correr())
    assert rotas == [], "Consultas que PostgreSQL no acepta: " + "; ".join(rotas)
