"""Database connection manager — supports SQLite (default) and PostgreSQL (DATABASE_URL).

IS_PG=False → aiosqlite, WAL mode, new connection per open_db() call.
IS_PG=True  → asyncpg connection pool (min=2, max=20).

Public API:
    await init_db(sqlite_path)   — call once at app startup
    await close_db_pool()        — call at app shutdown
    async with open_db() as conn — get an AsyncConn for queries
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional, Tuple

from app.storage.migrations.legacy import (
    _pre_migrate_sqlite,
    _rename_legacy_group_schema_pg,
    _rename_legacy_group_schema_sqlite,
)
from app.storage.migrations.postgres import run_postgres_migrations
from app.storage.migrations.sqlite import run_sqlite_migrations
from app.storage.schema import SCHEMA_PG, SCHEMA_SQLITE
from app.utils import flog

# ── Backend detection ──────────────────────────────────────────────────────────

DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL") or ""
IS_PG: bool = bool(DATABASE_URL)

# Placeholder for parameterised queries — always use ? (AsyncConn translates to $N for PG)
PH: str = "?"


def _db_error_types() -> tuple[type[BaseException], ...]:
    """Excepciones que puede lanzar el driver activo, para capturar un fallo de
    BD sin recurrir a ``except Exception``.

    Los dos drivers se prueban por separado: en SQLite `asyncpg` puede no estar
    instalado, y al revés. Importar aquí y no arriba evita que un backend
    obligue a tener el driver del otro.
    """
    tipos: list[type[BaseException]] = []
    try:
        import sqlite3

        tipos.append(sqlite3.Error)
    except ImportError:  # pragma: no cover - sqlite3 es de la stdlib
        pass
    try:
        import asyncpg

        tipos.append(asyncpg.PostgresError)
    except ImportError:  # pragma: no cover - solo en despliegues sin PG
        pass
    return tuple(tipos)


# Tupla lista para `except DB_ERRORS:`. Se calcula una vez al importar.
DB_ERRORS: tuple[type[BaseException], ...] = _db_error_types()

# ── Async connection layer ─────────────────────────────────────────────────────

_pg_pool: Any = None
_sqlite_path: Optional[Path] = None


class AsyncConn:
    """Unified async DB connection wrapper over asyncpg (PG) and aiosqlite (SQLite).

    Supports:
        await conn.execute(query, params)       — DML / DDL
        await conn.fetchone(query, params)      — one row or None
        await conn.fetchall(query, params)      — all rows
        await conn.fetchval(query, params)      — first column of first row
        await conn.executemany(query, list)     — batch insert/update
        await conn.commit()                     — commit (SQLite; no-op for PG)
        async with conn.transaction(): ...      — atomic block

    Row objects support both dict-style (row["col"]) and integer-index (row[0]) access.
    Use ? as placeholder in all queries — translated to $N automatically for PG.
    """

    def __init__(self, conn: Any, is_pg: bool) -> None:
        self._conn = conn
        self._is_pg = is_pg

    def _pg_sql(self, query: str) -> str:
        """Translate ? or %s placeholders to $1, $2, ... for asyncpg."""
        i = 0

        def _repl(m: re.Match) -> str:
            nonlocal i
            i += 1
            return f"${i}"

        return re.sub(r"\?|%s", _repl, query)

    async def execute(self, query: str, params: Tuple = ()) -> None:
        if self._is_pg:
            await self._conn.execute(self._pg_sql(query), *params)
        else:
            await self._conn.execute(query, params)

    async def fetchone(self, query: str, params: Tuple = ()) -> Optional[Any]:
        if self._is_pg:
            return await self._conn.fetchrow(self._pg_sql(query), *params)
        async with self._conn.execute(query, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, query: str, params: Tuple = ()) -> List[Any]:
        if self._is_pg:
            return await self._conn.fetch(self._pg_sql(query), *params)
        async with self._conn.execute(query, params) as cur:
            return await cur.fetchall()

    async def fetchval(self, query: str, params: Tuple = (), column: int = 0) -> Any:
        if self._is_pg:
            return await self._conn.fetchval(
                self._pg_sql(query), *params, column=column
            )
        async with self._conn.execute(query, params) as cur:
            row = await cur.fetchone()
            return row[column] if row is not None else None

    async def executemany(self, query: str, params_list: list) -> None:
        if self._is_pg:
            await self._conn.executemany(
                self._pg_sql(query), [tuple(p) for p in params_list]
            )
        else:
            await self._conn.executemany(query, params_list)

    async def commit(self) -> None:
        """Commit current transaction. No-op for asyncpg (auto-commits per statement)."""
        if not self._is_pg:
            await self._conn.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None, None]:
        """Atomic block. For PG uses asyncpg transaction; for SQLite commits on exit."""
        if self._is_pg:
            async with self._conn.transaction():
                yield
        else:
            await self._conn.execute("BEGIN")
            try:
                yield
            except Exception:
                await self._conn.rollback()
                raise
            else:
                await self._conn.commit()


# ── Lifecycle ──────────────────────────────────────────────────────────────────


async def migrate_schema(sqlite_path: Optional[Path] = None) -> None:
    """Crea/actualiza el esquema (tablas, índices, migraciones). Debe correr
    una sola vez por despliegue — con GAIA_WORKERS>1, main.py la llama en el
    proceso maestro antes de lanzar los workers (cada uno es un proceso propio
    que si no se le avisa via GAIA_SCHEMA_MIGRATED, re-ejecutaría esto y
    competiría por crear los mismos índices contra la misma DB recién creada:
    'malformed database schema ... already exists')."""
    if IS_PG:
        import asyncpg  # type: ignore[import]

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            async with conn.transaction():
                await _rename_legacy_group_schema_pg(conn)
                for stmt in SCHEMA_PG.split(";"):
                    s = stmt.strip()
                    if s:
                        await conn.execute(s)
                await run_postgres_migrations(conn)
        finally:
            await conn.close()
        flog.ok("[db] esquema PostgreSQL migrado")
    else:
        import sqlite3

        import aiosqlite  # type: ignore[import]

        path = sqlite_path or _sqlite_path
        if path is None:
            raise RuntimeError(
                "migrate_schema() requires sqlite_path when not using PostgreSQL"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await _rename_legacy_group_schema_sqlite(conn)
            # Pre-migration: add columns that SCHEMA_SQLITE references in
            # CREATE INDEX statements BEFORE executescript runs. Without this,
            # running executescript on an existing DB that lacks a new column
            # raises OperationalError ("no such column") because CREATE TABLE
            # IF NOT EXISTS is a no-op on existing tables.
            await _pre_migrate_sqlite(conn)
            await conn.executescript(SCHEMA_SQLITE)
            await run_sqlite_migrations(conn)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_connections_owner ON connections(owner_id)"
            )
            await conn.commit()
        flog.ok("[db] esquema SQLite migrado")


async def init_db(sqlite_path: Optional[Path] = None) -> None:
    """Initialize this process's DB connection/pool. Call once per worker.

    Runs migrate_schema() too, salvo que GAIA_SCHEMA_MIGRATED=1 (puesto por
    main.py tras migrar una sola vez en el proceso maestro antes de lanzar
    los workers) — ver migrate_schema() para el porqué.
    """
    global _pg_pool, _sqlite_path
    already_migrated = os.environ.get("GAIA_SCHEMA_MIGRATED") == "1"

    if IS_PG:
        import asyncpg  # type: ignore[import]

        if not already_migrated:
            await migrate_schema()
        _pg_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=20,
            command_timeout=30,
        )
        flog.ok("[db] asyncpg pool iniciado")
    else:
        if sqlite_path:
            _sqlite_path = sqlite_path
        if _sqlite_path is None:
            raise RuntimeError(
                "init_db() requires sqlite_path when not using PostgreSQL"
            )
        if not already_migrated:
            await migrate_schema(_sqlite_path)
        flog.ok("[db] aiosqlite inicializado")


async def close_db_pool() -> None:
    """Close DB connections. Call at app shutdown."""
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
        flog.info("[db] asyncpg pool cerrado")


# ── Public context manager ─────────────────────────────────────────────────────


@asynccontextmanager
async def _open_db_cm() -> AsyncGenerator[AsyncConn, None]:
    """Internal async context manager. Use open_db() publicly."""
    if IS_PG:
        if _pg_pool is None:
            raise RuntimeError("DB pool not initialized — call init_db() at startup")
        async with _pg_pool.acquire() as conn:
            yield AsyncConn(conn, is_pg=True)
    else:
        import sqlite3

        import aiosqlite  # type: ignore[import]

        if _sqlite_path is None:
            raise RuntimeError("SQLite path not set — call init_db(path) at startup")
        async with aiosqlite.connect(str(_sqlite_path)) as conn:
            conn.row_factory = sqlite3.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA busy_timeout=5000")
            yield AsyncConn(conn, is_pg=False)


def open_db() -> Any:
    """Async context manager that returns an AsyncConn for queries.

    Usage:
        async with open_db() as conn:
            row = await conn.fetchone("SELECT ...", (val,))
    """
    return _open_db_cm()
