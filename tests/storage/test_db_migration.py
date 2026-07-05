"""Regresión: init_db no debe fallar en DBs existentes con columnas faltantes.

_pre_migrate_sqlite añade columnas críticas ANTES de executescript para evitar
que CREATE INDEX falle sobre una tabla que no tiene la columna todavía.

Caso concreto: stripe_customer_id en users (commit de Stripe billing).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_old_db(path: Path) -> None:
    """DB con tabla users SIN stripe_customer_id (pre-Stripe)."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username   TEXT PRIMARY KEY,
            email      TEXT UNIQUE NOT NULL,
            role       TEXT NOT NULL DEFAULT 'standard',
            is_active  INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS connections (
            id         TEXT PRIMARY KEY,
            owner_id   TEXT NOT NULL DEFAULT 'admin',
            data       TEXT NOT NULL,
            tokens_in  INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO users (username, email, role)
        VALUES ('admin', 'admin@localhost', 'admin');
    """)
    conn.commit()
    conn.close()


def _user_cols(path: Path) -> set:
    conn = sqlite3.connect(str(path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    conn.close()
    return cols


# ── Tests de _pre_migrate_sqlite ───────────────────────────────────────────────


async def test_pre_migrate_adds_stripe_customer_id(tmp_path):
    """_pre_migrate_sqlite añade stripe_customer_id en DBs que no la tienen."""
    import app.storage.db as db_mod

    db = tmp_path / "old.db"
    _make_old_db(db)
    assert "stripe_customer_id" not in _user_cols(db)

    async with aiosqlite.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await db_mod._pre_migrate_sqlite(conn)
        await conn.commit()

    assert "stripe_customer_id" in _user_cols(db)


async def test_pre_migrate_is_noop_on_fresh_empty_db(tmp_path):
    """_pre_migrate_sqlite no crea tablas ni falla en una DB vacía."""
    import app.storage.db as db_mod

    db = tmp_path / "fresh.db"

    async with aiosqlite.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await db_mod._pre_migrate_sqlite(conn)
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in await cur.fetchall()]

    assert tables == [], "No debe crear tablas en una DB vacía"


async def test_pre_migrate_idempotent(tmp_path):
    """Llamar a _pre_migrate_sqlite dos veces no falla ni duplica columnas."""
    import app.storage.db as db_mod

    db = tmp_path / "idem.db"
    _make_old_db(db)

    for _ in range(2):
        async with aiosqlite.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await db_mod._pre_migrate_sqlite(conn)
            await conn.commit()

    all_names = [
        r[1]
        for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(users)").fetchall()
    ]
    assert all_names.count("stripe_customer_id") == 1


async def test_pre_migrate_ok_when_column_already_exists(tmp_path):
    """_pre_migrate_sqlite no falla si stripe_customer_id ya existe."""
    import app.storage.db as db_mod

    db = tmp_path / "already.db"
    conn_s = sqlite3.connect(str(db))
    conn_s.executescript("""
        CREATE TABLE users (
            username           TEXT PRIMARY KEY,
            email              TEXT NOT NULL,
            stripe_customer_id TEXT,
            created_at         TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn_s.commit()
    conn_s.close()

    async with aiosqlite.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await db_mod._pre_migrate_sqlite(conn)
        await conn.commit()


async def test_future_dep_in_schema_index_deps_applied(tmp_path, monkeypatch):
    """Nuevas entradas en _SCHEMA_INDEX_DEPS se aplican en DBs existentes."""
    import app.storage.db as db_mod

    db = tmp_path / "future.db"
    _make_old_db(db)

    original = list(db_mod._SCHEMA_INDEX_DEPS)
    monkeypatch.setattr(
        db_mod,
        "_SCHEMA_INDEX_DEPS",
        original + [("users", "future_regression_col", "TEXT")],
    )

    async with aiosqlite.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await db_mod._pre_migrate_sqlite(conn)
        await conn.commit()

    assert "future_regression_col" in _user_cols(db)


# ── Tests de _SCHEMA_INDEX_DEPS ────────────────────────────────────────────────


def test_schema_index_deps_covers_stripe():
    """_SCHEMA_INDEX_DEPS debe contener la entrada stripe_customer_id."""
    from app.storage.db import _SCHEMA_INDEX_DEPS

    entry = next(
        (
            e
            for e in _SCHEMA_INDEX_DEPS
            if e[0] == "users" and e[1] == "stripe_customer_id"
        ),
        None,
    )
    assert entry is not None, (
        "_SCHEMA_INDEX_DEPS debe incluir ('users', 'stripe_customer_id', ...)."
    )


def test_schema_index_deps_well_formed():
    """Cada entrada de _SCHEMA_INDEX_DEPS tiene exactamente 3 campos."""
    from app.storage.db import _SCHEMA_INDEX_DEPS

    for entry in _SCHEMA_INDEX_DEPS:
        assert len(entry) == 3, f"Entrada mal formada: {entry!r}"
        table, col, defn = entry
        assert isinstance(table, str) and table
        assert isinstance(col, str) and col
        assert isinstance(defn, str) and defn


# ── Tests de init_db sobre DB antigua ─────────────────────────────────────────


async def test_init_db_succeeds_on_old_db_missing_stripe(tmp_path):
    """Regresión principal: init_db no lanza en DB sin stripe_customer_id."""
    import app.storage.db as db_mod

    db = tmp_path / "pre_stripe.db"
    _make_old_db(db)

    old_path = db_mod._sqlite_path
    try:
        await db_mod.init_db(db)
    finally:
        db_mod._sqlite_path = old_path

    assert "stripe_customer_id" in _user_cols(db)


async def test_init_db_succeeds_on_fresh_db(tmp_path):
    """init_db funciona en una DB completamente nueva."""
    import app.storage.db as db_mod

    db = tmp_path / "brand_new.db"
    old_path = db_mod._sqlite_path
    try:
        await db_mod.init_db(db)
    finally:
        db_mod._sqlite_path = old_path

    assert "stripe_customer_id" in _user_cols(db)
