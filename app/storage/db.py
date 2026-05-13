"""SQLite connection manager — WAL mode, process-scoped singleton per path."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_lock = threading.Lock()
_pool: dict[str, sqlite3.Connection] = {}

# Tablas e índices que NO dependen de columnas añadidas por migración
_SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL DEFAULT 'admin',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    owner_id    TEXT NOT NULL DEFAULT 'admin',
    provider    TEXT NOT NULL,
    data        TEXT NOT NULL,
    linked_at   TEXT NOT NULL,
    PRIMARY KEY (owner_id, provider)
);
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user_agent
    ON conversations(user_id, agent_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_msg_conv
    ON messages(conversation_id, created_at ASC);
CREATE TABLE IF NOT EXISTS knowledge_items (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL DEFAULT 'admin',
    type       TEXT NOT NULL,
    title      TEXT NOT NULL,
    source     TEXT NOT NULL,
    content    TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner
    ON knowledge_items(owner_id, type, created_at DESC);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Migraciones incrementales para bases de datos ya existentes."""
    # 1. Añadir owner_id a connections si no existe
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(connections)")}
    if "owner_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE connections ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'admin'"
        )
        conn.commit()

    # 2. Recrear accounts con PK compuesta (owner_id, provider) si aún usa PK simple
    acct_cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    if "owner_id" not in acct_cols:
        conn.executescript("""
            ALTER TABLE accounts RENAME TO _accounts_old;
            CREATE TABLE accounts (
                owner_id    TEXT NOT NULL DEFAULT 'admin',
                provider    TEXT NOT NULL,
                data        TEXT NOT NULL,
                linked_at   TEXT NOT NULL,
                PRIMARY KEY (owner_id, provider)
            );
            INSERT INTO accounts
                SELECT 'admin', provider, data, linked_at FROM _accounts_old;
            DROP TABLE _accounts_old;
        """)
        conn.commit()


def open_db(path: Path) -> sqlite3.Connection:
    """Devuelve (o crea) la conexión SQLite compartida para *path*."""
    key = str(path.resolve())
    if key not in _pool:
        with _lock:
            if key not in _pool:
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(key, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.executescript(_SCHEMA)
                _migrate(conn)
                # Índice sobre owner_id — se crea después de garantizar que la columna existe
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_connections_owner "
                    "ON connections(owner_id)"
                )
                conn.commit()
                _pool[key] = conn
    return _pool[key]
