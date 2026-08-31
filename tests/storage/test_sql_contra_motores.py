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
from uuid import uuid4

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


@pytest.mark.skipif(
    not DSN, reason="define GAIA_TEST_PG_DSN para probar la migración 44"
)
def test_migration_44_is_recorded_and_builds_indexes_in_real_postgres():
    """Aplica dos arranques sobre un esquema PG persistente y verifica la 44."""
    import asyncio

    import asyncpg

    from app.storage.migrations.postgres import run_postgres_migrations

    async def _correr() -> None:
        conn = await asyncpg.connect(DSN)
        schema = f"cursor_completion_{uuid4().hex}"
        try:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f'SET search_path TO "{schema}"')
            for sentencia in schema_for("pg").split(";"):
                if sentencia.strip():
                    await conn.execute(sentencia)

            first = await run_postgres_migrations(conn)
            second = await run_postgres_migrations(conn)
            migration = await conn.fetchrow(
                "SELECT name FROM schema_migrations WHERE version=44"
            )
            indexes = {
                row["indexname"]
                for row in await conn.fetch(
                    "SELECT indexname FROM pg_indexes WHERE schemaname=$1", schema
                )
            }

            assert 44 in first
            assert 44 not in second
            assert migration["name"] == "cursor_completion_indexes"
            assert {
                "idx_rsoc_feed_page",
                "idx_connections_updated_page",
                "idx_llm_orchestrations_updated_page",
            } <= indexes
        finally:
            await conn.execute("SET search_path TO public")
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await conn.close()

    asyncio.run(_correr())


# Fragmentos de SQL que se arman en Python y por tanto no están en `app/sql/`,
# así que el catálogo de arriba no los ve. Cada uno va con el envoltorio mínimo
# que lo convierte en una consulta preparable.
#
# `PUBLICLY_AVAILABLE_SQL` entró aquí después de tumbar el perfil público con un
# 500 en PostgreSQL: llevaba `NOT inactive_resource.is_active` y esa columna se
# declara `@BOOL@`, que es INTEGER en SQLite —que lo acepta— y SMALLINT en
# PostgreSQL, que responde «argument of NOT must be type boolean». Un fragmento
# que solo vive en Python no lo prepara nadie, y PostgreSQL solo corre aquí
# cuando hay DSN: dos capas de ceguera sobre la misma línea.
def _fragmentos_construidos_en_python() -> list[tuple[str, str]]:
    from app.api.routes.explore._shared import STARRED_BY_REQUESTER
    from app.services.admin_listings import connections_spec
    from app.services.admin_resource_cursor_listing import OWNER, ROW
    from app.services.social_catalog import PUBLICLY_AVAILABLE_SQL

    fragmentos = [
        (
            "social_catalog:PUBLICLY_AVAILABLE_SQL",
            f"SELECT 1 FROM resource_social WHERE {PUBLICLY_AVAILABLE_SQL}",
        ),
        (
            "explore/_shared:STARRED_BY_REQUESTER",
            f"SELECT {STARRED_BY_REQUESTER} FROM resource_social",
        ),
    ]
    # Los listados del panel arman su SELECT en Python, así que el barrido de
    # `app/sql/` no los ve. Es el mismo punto ciego por el que un `NOT` sobre
    # `@BOOL@` —INTEGER en SQLite, SMALLINT en PostgreSQL— tumbó el perfil
    # público: pasaba en un motor y era un 500 en el otro.
    for spec in (connections_spec(),):
        fragmentos.append(
            (
                f"admin_listings:{spec.table}",
                f"SELECT {spec.columns} FROM {spec.table} {ROW} "
                f"LEFT JOIN users {OWNER} "
                f"ON {OWNER}.id = {ROW}.{spec.owner_column} WHERE 1=1",
            )
        )
    return fragmentos


def test_el_sql_armado_en_python_prepara_en_sqlite(tmp_path):
    conn = sqlite3.connect(tmp_path / "fragmentos.db")
    rotas: list[str] = []
    try:
        conn.executescript(schema_for("sqlite"))
        for identificador, sentencia in _fragmentos_construidos_en_python():
            try:
                conn.execute(f"EXPLAIN {sentencia}", [None] * sentencia.count("?"))
            except sqlite3.Error as exc:
                rotas.append(f"{identificador}: {exc}")
    finally:
        conn.close()

    assert rotas == [], "Fragmentos que SQLite no acepta: " + "; ".join(rotas)


@pytest.mark.skipif(
    not DSN,
    reason="define GAIA_TEST_PG_DSN para preparar los fragmentos en PostgreSQL",
)
def test_el_sql_armado_en_python_prepara_en_postgres():
    import asyncio

    import asyncpg

    from app.storage.db import AsyncConn
    from app.storage.migrations.postgres import run_postgres_migrations

    traducir = AsyncConn(None, True)._pg_sql

    async def _correr() -> list[str]:
        conn = await asyncpg.connect(DSN)
        rotas: list[str] = []
        schema = f"fragmentos_{uuid4().hex}"
        try:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f'SET search_path TO "{schema}"')
            for sentencia in schema_for("pg").split(";"):
                if sentencia.strip():
                    await conn.execute(sentencia)
            await run_postgres_migrations(conn)
            for identificador, sentencia in _fragmentos_construidos_en_python():
                try:
                    await conn.prepare(traducir(sentencia))
                except asyncpg.PostgresError as exc:
                    rotas.append(f"{identificador}: {exc}")
        finally:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await conn.close()
        return rotas

    rotas = asyncio.run(_correr())
    assert rotas == [], "Fragmentos que PostgreSQL no acepta: " + "; ".join(rotas)


@pytest.mark.skipif(
    not DSN, reason="define GAIA_TEST_PG_DSN para probar la migración PostgreSQL"
)
def test_social_iso_dates_migrates_a_real_postgres():
    import asyncio

    import asyncpg

    from app.storage.migrations.steps.social import _social_iso_dates_pg

    async def _correr() -> None:
        conn = await asyncpg.connect(DSN)
        schema = f"social_dates_{uuid4().hex}"
        try:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f'SET search_path TO "{schema}"')
            await conn.execute("""
                CREATE TABLE resource_social (
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    PRIMARY KEY (resource_type, resource_id, owner)
                );
                CREATE TABLE resource_stars (
                    username TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (username, resource_type, resource_id)
                );
                CREATE TABLE user_follows (
                    follower TEXT NOT NULL,
                    following TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (follower, following)
                );
                INSERT INTO resource_social VALUES ('agent', 'a1', 'alice', NOW());
                INSERT INTO resource_stars (username, resource_type, resource_id)
                    VALUES ('alice', 'agent', 'a1');
                INSERT INTO user_follows (follower, following)
                    VALUES ('alice', 'bob');
            """)

            await _social_iso_dates_pg(conn)

            column = await conn.fetchrow(
                "SELECT data_type,is_nullable,column_default "
                "FROM information_schema.columns "
                "WHERE table_schema=current_schema() "
                "AND table_name='resource_social' AND column_name='updated_at'"
            )
            social_date = await conn.fetchval(
                "SELECT updated_at FROM resource_social"
            )
            star_date = await conn.fetchval("SELECT created_at FROM resource_stars")
            follow_date = await conn.fetchval("SELECT created_at FROM user_follows")

            assert column["data_type"] == "text"
            assert column["is_nullable"] == "NO"
            assert "to_char" in column["column_default"]
            assert re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", social_date
            )
            for value in (star_date, follow_date):
                assert re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value
                )
        finally:
            await conn.execute("SET search_path TO public")
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await conn.close()

    asyncio.run(_correr())
