"""Regresión: init_db no debe fallar en DBs existentes con columnas faltantes.

_pre_migrate_sqlite añade columnas críticas ANTES de executescript para evitar
que CREATE INDEX falle sobre una tabla que no tiene la columna todavía.

Caso concreto: stripe_customer_id en users (commit de Stripe billing).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

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
        VALUES ('admin', 'admin@example.com', 'admin');
    """)
    conn.commit()
    conn.close()


def _user_cols(path: Path) -> set:
    conn = sqlite3.connect(str(path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    conn.close()
    return cols


# ── Tests de _pre_migrate_sqlite ───────────────────────────────────────────────


async def test_add_sqlite_column_is_idempotent(tmp_path):
    import app.storage.db_migrations as migration_mod

    async with aiosqlite.connect(tmp_path / "columns.db") as conn:
        await conn.execute("CREATE TABLE sample (id TEXT PRIMARY KEY)")

        assert await migration_mod._add_sqlite_column(conn, "sample", "label", "TEXT")
        assert not await migration_mod._add_sqlite_column(
            conn, "sample", "label", "TEXT"
        )


async def test_add_sqlite_column_propagates_unexpected_failure(tmp_path):
    import app.storage.db_migrations as migration_mod

    async with aiosqlite.connect(tmp_path / "broken-column.db") as conn:
        await conn.execute("CREATE TABLE sample (id TEXT PRIMARY KEY)")

        with pytest.raises(sqlite3.OperationalError):
            await migration_mod._add_sqlite_column(
                conn, "sample", "broken", "TEXT CHECK ("
            )


async def test_pre_migrate_adds_stripe_customer_id(tmp_path):
    """_pre_migrate_sqlite añade stripe_customer_id en DBs que no la tienen."""
    import app.storage.db_migrations as migration_mod

    db = tmp_path / "old.db"
    _make_old_db(db)
    assert "stripe_customer_id" not in _user_cols(db)

    async with aiosqlite.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await migration_mod._pre_migrate_sqlite(conn)
        await conn.commit()

    assert "stripe_customer_id" in _user_cols(db)


async def test_pre_migrate_is_noop_on_fresh_empty_db(tmp_path):
    """_pre_migrate_sqlite no crea tablas ni falla en una DB vacía."""
    import app.storage.db_migrations as migration_mod

    db = tmp_path / "fresh.db"

    async with aiosqlite.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await migration_mod._pre_migrate_sqlite(conn)
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in await cur.fetchall()]

    assert tables == [], "No debe crear tablas en una DB vacía"


async def test_pre_migrate_idempotent(tmp_path):
    """Llamar a _pre_migrate_sqlite dos veces no falla ni duplica columnas."""
    import app.storage.db_migrations as migration_mod

    db = tmp_path / "idem.db"
    _make_old_db(db)

    for _ in range(2):
        async with aiosqlite.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await migration_mod._pre_migrate_sqlite(conn)
            await conn.commit()

    all_names = [
        r[1]
        for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(users)").fetchall()
    ]
    assert all_names.count("stripe_customer_id") == 1


async def test_pre_migrate_ok_when_column_already_exists(tmp_path):
    """_pre_migrate_sqlite no falla si stripe_customer_id ya existe."""
    import app.storage.db_migrations as migration_mod

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
        await migration_mod._pre_migrate_sqlite(conn)
        await conn.commit()


async def test_migrate_existing_knowledge_table_adds_checksum_before_its_index(
    tmp_path,
):
    """Una DB anterior a checksum debe arrancar y completar la migración 19."""
    import app.storage.db as db_mod
    from app.storage.schema import SCHEMA_SQLITE

    db = tmp_path / "pre-checksum.db"
    legacy_schema = SCHEMA_SQLITE.replace(
        "    checksum   TEXT NOT NULL DEFAULT '',\n", ""
    )
    conn = sqlite3.connect(str(db))
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO knowledge_items "
        "(id,owner_id,type,title,source,content,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "legacy-knowledge",
            "alice",
            "text",
            "Legacy",
            "",
            "contenido anterior",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.commit()
    conn.close()

    await db_mod.migrate_schema(db)

    migrated = sqlite3.connect(str(db))
    columns = {row[1] for row in migrated.execute("PRAGMA table_info(knowledge_items)")}
    checksum = migrated.execute(
        "SELECT checksum FROM knowledge_items WHERE id='legacy-knowledge'"
    ).fetchone()[0]
    indexes = {row[1] for row in migrated.execute("PRAGMA index_list(knowledge_items)")}
    migrated.close()

    assert "checksum" in columns
    assert checksum == hashlib.sha256(b"contenido anterior").hexdigest()
    assert "idx_knowledge_checksum" in indexes


async def test_migration_removes_legacy_resource_folders(tmp_path):
    """The removed folder model and its catalog metadata do not survive upgrade."""
    import app.storage.db as db_mod

    db = tmp_path / "legacy-folders.db"
    await db_mod.migrate_schema(db)

    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE resource_folders (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            section TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE resource_folder_items (
            folder_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL
        );
        INSERT INTO resource_folders VALUES ('old-folder', 'alice', 'document', 'Old');
        INSERT INTO resource_folder_items VALUES ('old-folder', 'knowledge', 'doc-1');
        INSERT INTO resource_stars (username, resource_type, resource_id)
            VALUES ('alice', 'knowledge', 'old-folder');
        INSERT INTO resource_social (resource_type, resource_id, owner, name)
            VALUES ('knowledge', 'old-folder', 'alice', 'Old');
    """)
    conn.commit()
    conn.close()

    await db_mod.migrate_schema(db)

    migrated = sqlite3.connect(str(db))
    tables = {
        row[0]
        for row in migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    stars = migrated.execute(
        "SELECT COUNT(*) FROM resource_stars WHERE resource_id='old-folder'"
    ).fetchone()[0]
    social = migrated.execute(
        "SELECT COUNT(*) FROM resource_social WHERE resource_id='old-folder'"
    ).fetchone()[0]
    migrated.close()

    assert "resource_folders" not in tables
    assert "resource_folder_items" not in tables
    assert stars == 0
    assert social == 0


async def test_migration_mirrors_legacy_agent_language_into_labels(tmp_path):
    import app.storage.db as db_mod

    db = tmp_path / "legacy-agent-language.db"
    await db_mod.migrate_schema(db)

    conn = sqlite3.connect(str(db))
    payload = {
        "id": "legacy-agent",
        "name": "Legacy Spanish agent",
        "language": "es",
        "labels": ["private"],
    }
    conn.execute(
        "INSERT INTO agents "
        "(id, owner_id, name, scope, data, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-agent",
            "alice",
            payload["name"],
            "private",
            json.dumps(payload),
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.commit()
    conn.close()

    await db_mod.migrate_schema(db)

    migrated = sqlite3.connect(str(db))
    stored = json.loads(
        migrated.execute("SELECT data FROM agents WHERE id='legacy-agent'").fetchone()[
            0
        ]
    )
    indexed = migrated.execute(
        "SELECT COUNT(*) FROM resource_labels "
        "WHERE resource_type='agent' AND resource_id='legacy-agent' "
        "AND label='lang_es'"
    ).fetchone()[0]
    migrated.close()

    assert stored["labels"] == ["private", "community", "lang_es"]
    assert indexed == 1


async def test_migration_removes_folders_from_database_without_social_tables(tmp_path):
    """Folder removal also works for databases older than the social catalog."""
    import app.storage.db as db_mod

    db = tmp_path / "very-old-folders.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE resource_folders (id TEXT PRIMARY KEY);
        CREATE TABLE resource_folder_items (folder_id TEXT NOT NULL);
        INSERT INTO resource_folders VALUES ('old-folder');
        INSERT INTO resource_folder_items VALUES ('old-folder');
    """)
    conn.commit()
    conn.close()

    await db_mod.migrate_schema(db)

    migrated = sqlite3.connect(str(db))
    tables = {
        row[0]
        for row in migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    migrated.close()
    assert "resource_folders" not in tables
    assert "resource_folder_items" not in tables


async def test_future_dep_in_schema_index_deps_applied(tmp_path, monkeypatch):
    """Nuevas entradas en _SCHEMA_INDEX_DEPS se aplican en DBs existentes."""
    import app.storage.db_migrations as migration_mod

    db = tmp_path / "future.db"
    _make_old_db(db)

    original = list(migration_mod._SCHEMA_INDEX_DEPS)
    monkeypatch.setattr(
        migration_mod,
        "_SCHEMA_INDEX_DEPS",
        original + [("users", "future_regression_col", "TEXT")],
    )

    async with aiosqlite.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await migration_mod._pre_migrate_sqlite(conn)
        await conn.commit()

    assert "future_regression_col" in _user_cols(db)


# ── Tests de _SCHEMA_INDEX_DEPS ────────────────────────────────────────────────


def test_schema_index_deps_covers_stripe():
    """_SCHEMA_INDEX_DEPS debe contener la entrada stripe_customer_id."""
    from app.storage.db_migrations import _SCHEMA_INDEX_DEPS

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
    from app.storage.db_migrations import _SCHEMA_INDEX_DEPS

    for entry in _SCHEMA_INDEX_DEPS:
        assert len(entry) == 3, f"Entrada mal formada: {entry!r}"
        table, col, defn = entry
        assert isinstance(table, str) and table
        assert isinstance(col, str) and col
        assert isinstance(defn, str) and defn


async def test_legacy_group_tables_keep_their_data(tmp_path):
    """The terminology migration renames tables, columns and keeps every row."""
    import app.storage.db_migrations as migration_mod

    db = tmp_path / "legacy-groups.db"
    legacy_scope = "work" + "space"
    legacy_id = f"{legacy_scope}_id"
    conn_s = sqlite3.connect(str(db))
    conn_s.executescript(f"""
        CREATE TABLE "{legacy_scope}s" (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE "{legacy_scope}_members" (
            "{legacy_id}" TEXT NOT NULL,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            permissions TEXT NOT NULL DEFAULT '{{}}',
            joined_at TEXT NOT NULL,
            PRIMARY KEY ("{legacy_id}", username)
        );
        INSERT INTO "{legacy_scope}s"
            VALUES ('group-1', 'Equipo', 'alice', '2026-01-01', 'active');
        INSERT INTO "{legacy_scope}_members"
            VALUES ('group-1', 'alice', 'owner', '{{}}', '2026-01-01');
    """)
    conn_s.commit()
    conn_s.close()

    async with aiosqlite.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        await migration_mod._rename_legacy_group_schema_sqlite(conn)

    migrated = sqlite3.connect(str(db))
    tables = {
        row[0]
        for row in migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    member_columns = {
        row[1] for row in migrated.execute("PRAGMA table_info(group_members)")
    }
    group_row = migrated.execute("SELECT id, name, created_by FROM groups").fetchone()
    member_row = migrated.execute(
        "SELECT group_id, username, role FROM group_members"
    ).fetchone()
    migrated.close()

    assert f"{legacy_scope}s" not in tables
    assert f"{legacy_scope}_members" not in tables
    assert legacy_id not in member_columns
    assert "group_id" in member_columns
    assert group_row == ("group-1", "Equipo", "alice")
    assert member_row == ("group-1", "alice", "owner")


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
