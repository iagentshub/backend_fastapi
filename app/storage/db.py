"""Database connection manager — supports SQLite (default) and PostgreSQL (DATABASE_URL).

IS_PG=False  → SQLite WAL, process-scoped singleton per path (original behaviour).
IS_PG=True   → psycopg2 ThreadedConnectionPool, path argument ignored.
"""
from __future__ import annotations

import os

from app.utils import flog
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

# ── Backend detection ──────────────────────────────────────────────────────────

DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL") or ""
IS_PG: bool = bool(DATABASE_URL)

# Placeholder for parameterised queries
PH: str = "%s" if IS_PG else "?"

# ── Schema (shared DDL — uses TEXT placeholders adapted below) ─────────────────

_SCHEMA_SQLITE = """
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
    folder_id  TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner
    ON knowledge_items(owner_id, type, created_at DESC);
CREATE TABLE IF NOT EXISTS knowledge_folders (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    section    TEXT NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kf_owner
    ON knowledge_folders(owner_id, section);
CREATE TABLE IF NOT EXISTS users (
    username           TEXT PRIMARY KEY,
    email              TEXT UNIQUE NOT NULL,
    password_hash      TEXT,
    display_name       TEXT,
    birth_date         TEXT,
    gender             TEXT,
    country            TEXT,
    phone              TEXT,
    provider           TEXT,
    provider_sub       TEXT,
    role               TEXT NOT NULL DEFAULT 'standard',
    is_active          INTEGER NOT NULL DEFAULT 1,
    is_verified        INTEGER NOT NULL DEFAULT 1,
    verification_token TEXT,
    reset_token        TEXT,
    reset_token_expires TEXT,
    preferences        TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
"""

_SCHEMA_PG = """
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
    folder_id  TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner
    ON knowledge_items(owner_id, type, created_at DESC);
CREATE TABLE IF NOT EXISTS knowledge_folders (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    section    TEXT NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kf_owner
    ON knowledge_folders(owner_id, section);
CREATE TABLE IF NOT EXISTS users (
    username           TEXT PRIMARY KEY,
    email              TEXT UNIQUE NOT NULL,
    password_hash      TEXT,
    display_name       TEXT,
    birth_date         TEXT,
    gender             TEXT,
    country            TEXT,
    phone              TEXT,
    provider           TEXT,
    provider_sub       TEXT,
    role               TEXT NOT NULL DEFAULT 'standard',
    is_active          SMALLINT NOT NULL DEFAULT 1,
    is_verified        SMALLINT NOT NULL DEFAULT 1,
    verification_token TEXT,
    reset_token        TEXT,
    reset_token_expires TEXT,
    preferences        TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
"""

# ── SQLite backend ─────────────────────────────────────────────────────────────

_sqlite_lock = threading.Lock()
_sqlite_pool: dict[str, sqlite3.Connection] = {}


def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    """Incremental migrations for pre-existing SQLite databases."""
    # 1. Add owner_id to connections if missing
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(connections)")}
    if "owner_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE connections ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'admin'"
        )
        conn.commit()

    # 2. Recreate accounts with composite PK if it still uses a simple PK
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

    # 3. Add folder_id to knowledge_items if missing
    ki_cols = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_items)")}
    if "folder_id" not in ki_cols:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN folder_id TEXT")
        conn.commit()

    # 4. Add users table columns that may be missing in older DBs
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for col, definition in [
        ("birth_date", "TEXT"),
        ("gender", "TEXT"),
        ("country", "TEXT"),
        ("phone", "TEXT"),
        ("display_name", "TEXT"),
        ("provider", "TEXT"),
        ("provider_sub", "TEXT"),
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
        ("is_verified", "INTEGER NOT NULL DEFAULT 1"),
        ("verification_token", "TEXT"),
        ("reset_token", "TEXT"),
        ("reset_token_expires", "TEXT"),
        ("preferences", "TEXT"),
    ]:
        if col not in user_cols:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                conn.commit()
            except Exception as exc:
                flog.warning(f"[db] No se pudo añadir columna {col}: {exc}")


def _migrate_users_json_sqlite(conn: sqlite3.Connection) -> None:
    """Import users.json into the users table if it exists and the table is empty."""
    import json
    from pathlib import Path as _Path

    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count:
        return
    # Resolve users.json relative to the DB file path
    # Try environment variable fallback
    data_dir_env = os.environ.get("GAIA_DATA_DIR", "")
    if data_dir_env:
        users_json = _Path(data_dir_env) / "users.json"
    else:
        users_json = _Path("data") / "users.json"

    if not users_json.exists():
        return

    try:
        users = json.loads(users_json.read_text(encoding="utf-8"))
        from datetime import datetime, timezone

        for u in users:
            username = u.get("username", "")
            email = u.get("email") or f"{username}@migrated.local"
            conn.execute(
                "INSERT OR IGNORE INTO users "
                "(username, email, password_hash, display_name, birth_date, gender, "
                "country, phone, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    email,
                    u.get("password_hash"),
                    u.get("display_name"),
                    u.get("birth_date"),
                    u.get("gender"),
                    u.get("country"),
                    u.get("phone"),
                    u.get("role", "standard"),
                    1 if u.get("is_active", True) else 0,
                    u.get("created_at") or datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()
        users_json.rename(users_json.with_suffix(".migrated"))
    except Exception as exc:
        flog.warning(f"[db] Importación users.json (SQLite) fallida: {exc}")


def _open_sqlite(path: Path) -> sqlite3.Connection:
    key = str(path.resolve())
    if key not in _sqlite_pool:
        with _sqlite_lock:
            if key not in _sqlite_pool:
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(key, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.executescript(_SCHEMA_SQLITE)
                _migrate_sqlite(conn)
                _migrate_users_json_sqlite(conn)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_connections_owner "
                    "ON connections(owner_id)"
                )
                conn.commit()
                _sqlite_pool[key] = conn
    return _sqlite_pool[key]


# ── PostgreSQL backend ─────────────────────────────────────────────────────────

_pg_pool: Any = None
_pg_lock = threading.Lock()


def _ensure_pg_pool() -> Any:
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pg_lock:
        if _pg_pool is not None:
            return _pg_pool
        import psycopg2
        import psycopg2.extras
        import psycopg2.pool

        pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            dsn=DATABASE_URL,
        )
        # Run schema once
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                for statement in _SCHEMA_PG.split(";"):
                    stmt = statement.strip()
                    if stmt:
                        cur.execute(stmt)
            _migrate_pg(conn)
            _migrate_users_json_pg(conn)
            conn.commit()
        finally:
            pool.putconn(conn)
        _pg_pool = pool
    return _pg_pool


def _migrate_pg(conn: Any) -> None:
    """Incremental migrations for pre-existing PostgreSQL databases."""
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS folder_id TEXT")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TEXT")
    conn.commit()


def _migrate_users_json_pg(conn: Any) -> None:
    """Import users.json into the users table if it exists and the table is empty."""
    import json
    from pathlib import Path as _Path

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
    if count:
        return

    data_dir_env = os.environ.get("GAIA_DATA_DIR", "")
    users_json = _Path(data_dir_env) / "users.json" if data_dir_env else _Path("data") / "users.json"

    if not users_json.exists():
        return

    try:
        users = json.loads(users_json.read_text(encoding="utf-8"))
        from datetime import datetime, timezone

        with conn.cursor() as cur:
            for u in users:
                username = u.get("username", "")
                email = u.get("email") or f"{username}@migrated.local"
                cur.execute(
                    "INSERT INTO users "
                    "(username, email, password_hash, display_name, birth_date, gender, "
                    "country, phone, role, is_active, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (username) DO NOTHING",
                    (
                        username,
                        email,
                        u.get("password_hash"),
                        u.get("display_name"),
                        u.get("birth_date"),
                        u.get("gender"),
                        u.get("country"),
                        u.get("phone"),
                        u.get("role", "standard"),
                        1 if u.get("is_active", True) else 0,
                        u.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    ),
                )
        conn.commit()
        users_json.rename(users_json.with_suffix(".migrated"))
    except Exception as exc:
        flog.warning(f"[db] Importación users.json (PG) fallida: {exc}")


def _open_pg() -> Any:
    import psycopg2.extras

    pool = _ensure_pg_pool()
    conn = pool.getconn()
    # Use RealDictCursor so rows are dict-like (matches sqlite3.Row interface)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


# ── Public API ─────────────────────────────────────────────────────────────────


def open_db(path: Path) -> Any:
    """Return a DB connection.

    SQLite: returns (or creates) a process-scoped singleton for *path*.
    PostgreSQL: returns a connection from the pool; *path* is ignored.
    """
    if IS_PG:
        return _open_pg()
    return _open_sqlite(path)


def close_db(conn: Any) -> None:
    """Return connection to pool (PG) or close (SQLite — no-op, singleton)."""
    if IS_PG and _pg_pool is not None:
        try:
            _pg_pool.putconn(conn)
        except Exception as exc:
            flog.debug(f"[db] Error al liberar conexión PG al pool: {exc}")
    # SQLite: singleton is kept alive — nothing to do
