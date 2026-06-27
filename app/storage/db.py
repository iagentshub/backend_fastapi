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
    username              TEXT PRIMARY KEY,
    email                 TEXT UNIQUE NOT NULL,
    password_hash         TEXT,
    display_name          TEXT,
    birth_date            TEXT,
    gender                TEXT,
    country               TEXT,
    phone                 TEXT,
    provider              TEXT,
    provider_sub          TEXT,
    role                  TEXT NOT NULL DEFAULT 'standard',
    is_active             INTEGER NOT NULL DEFAULT 1,
    is_verified           INTEGER NOT NULL DEFAULT 1,
    verification_token    TEXT,
    reset_token           TEXT,
    reset_token_expires   TEXT,
    preferences           TEXT,
    deletion_requested_at TEXT,
    deletion_token        TEXT,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE TABLE IF NOT EXISTS workspace_groups (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE(workspace_id, name)
);
CREATE INDEX IF NOT EXISTS idx_ws_groups_ws ON workspace_groups(workspace_id);
CREATE TABLE IF NOT EXISTS workspace_group_members (
    group_id     TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    username     TEXT NOT NULL,
    joined_at    TEXT NOT NULL,
    PRIMARY KEY (group_id, username)
);
CREATE INDEX IF NOT EXISTS idx_wsgm_user ON workspace_group_members(username);
CREATE TABLE IF NOT EXISTS resource_groups (
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    shared_by     TEXT NOT NULL,
    shared_at     TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_rg_group    ON resource_groups(group_id, resource_type);
CREATE INDEX IF NOT EXISTS idx_rg_resource ON resource_groups(resource_type, resource_id);
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL,
    username     TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'member',
    joined_at    TEXT NOT NULL,
    PRIMARY KEY (workspace_id, username)
);
CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(username);
CREATE TABLE IF NOT EXISTS token_daily (
    day      TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    tokens   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_token_daily_owner ON token_daily(owner_id, day DESC);
CREATE TABLE IF NOT EXISTS workspace_invitations (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    invited_by   TEXT NOT NULL,
    username     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    UNIQUE(workspace_id, username)
);
CREATE INDEX IF NOT EXISTS idx_ws_inv_user ON workspace_invitations(username, status);
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
    is_active             SMALLINT NOT NULL DEFAULT 1,
    is_verified           SMALLINT NOT NULL DEFAULT 1,
    verification_token    TEXT,
    reset_token           TEXT,
    reset_token_expires   TEXT,
    preferences           TEXT,
    deletion_requested_at TEXT,
    deletion_token        TEXT,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE TABLE IF NOT EXISTS workspace_groups (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE(workspace_id, name)
);
CREATE INDEX IF NOT EXISTS idx_ws_groups_ws ON workspace_groups(workspace_id);
CREATE TABLE IF NOT EXISTS workspace_group_members (
    group_id     TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    username     TEXT NOT NULL,
    joined_at    TEXT NOT NULL,
    PRIMARY KEY (group_id, username)
);
CREATE INDEX IF NOT EXISTS idx_wsgm_user ON workspace_group_members(username);
CREATE TABLE IF NOT EXISTS resource_groups (
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    shared_by     TEXT NOT NULL,
    shared_at     TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_rg_group    ON resource_groups(group_id, resource_type);
CREATE INDEX IF NOT EXISTS idx_rg_resource ON resource_groups(resource_type, resource_id);
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL,
    username     TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'member',
    joined_at    TEXT NOT NULL,
    PRIMARY KEY (workspace_id, username)
);
CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(username);
CREATE TABLE IF NOT EXISTS token_daily (
    day      TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    tokens   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_token_daily_owner ON token_daily(owner_id, day DESC);
CREATE TABLE IF NOT EXISTS workspace_invitations (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    invited_by   TEXT NOT NULL,
    username     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    UNIQUE(workspace_id, username)
);
CREATE INDEX IF NOT EXISTS idx_ws_inv_user ON workspace_invitations(username, status);
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

    # 4. Create team tables if missing (new feature)
    existing_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "teams" not in existing_tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS teams (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                created_by  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS team_members (
                team_id     TEXT NOT NULL,
                username    TEXT NOT NULL,
                is_manager  INTEGER NOT NULL DEFAULT 0,
                permissions TEXT NOT NULL DEFAULT '{}',
                joined_at   TEXT NOT NULL,
                PRIMARY KEY (team_id, username)
            );
            CREATE INDEX IF NOT EXISTS idx_team_members_username ON team_members(username);
            CREATE TABLE IF NOT EXISTS team_invitations (
                id              TEXT PRIMARY KEY,
                team_id         TEXT NOT NULL,
                invited_email   TEXT NOT NULL,
                invited_by      TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                expires_at      TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_team_inv_email ON team_invitations(invited_email, status);
        """)
        conn.commit()

    # 5. Add users table columns that may be missing in older DBs
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
        ("deletion_requested_at", "TEXT"),
        ("deletion_token", "TEXT"),
    ]:
        if col not in user_cols:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                conn.commit()
            except Exception as exc:
                flog.warning(f"[db] No se pudo añadir columna {col}: {exc}")

    # 6. Create resource_teams if missing
    if "resource_teams" not in existing_tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS resource_teams (
                resource_type TEXT NOT NULL,
                resource_id   TEXT NOT NULL,
                team_id       TEXT NOT NULL,
                shared_by     TEXT NOT NULL,
                shared_at     TEXT NOT NULL,
                PRIMARY KEY (resource_type, resource_id, team_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rt_team ON resource_teams(team_id, resource_type);
            CREATE INDEX IF NOT EXISTS idx_rt_resource ON resource_teams(resource_type, resource_id);
        """)
        conn.commit()

    # 7. Create workspaces tables if missing
    if "workspaces" not in existing_tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                created_by  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspace_members (
                workspace_id TEXT NOT NULL,
                username     TEXT NOT NULL,
                role         TEXT NOT NULL DEFAULT 'member',
                joined_at    TEXT NOT NULL,
                PRIMARY KEY (workspace_id, username)
            );
            CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(username);
        """)
        conn.commit()

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_name_creator ON teams(name, created_by)"
    )
    conn.commit()

    # Refresh existing_tables after previous migrations
    existing_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # 8. Create token_daily table if missing
    if "token_daily" not in existing_tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_daily (
                day      TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                tokens   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, owner_id)
            );
            CREATE INDEX IF NOT EXISTS idx_token_daily_owner ON token_daily(owner_id, day DESC);
        """)
        conn.commit()

    # 9. Create workspace_invitations table if missing
    existing_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "workspace_invitations" not in existing_tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workspace_invitations (
                id           TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                invited_by   TEXT NOT NULL,
                username     TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL,
                UNIQUE(workspace_id, username)
            );
            CREATE INDEX IF NOT EXISTS idx_ws_inv_user ON workspace_invitations(username, status);
        """)
        conn.commit()

    # 10. Migrate teams → workspace_groups (drop old tables, create new)
    existing_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "workspace_groups" not in existing_tables:
        conn.executescript("""
            DROP TABLE IF EXISTS resource_teams;
            DROP TABLE IF EXISTS team_invitations;
            DROP TABLE IF EXISTS team_members;
            DROP TABLE IF EXISTS teams;
            CREATE TABLE IF NOT EXISTS workspace_groups (
                id           TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name         TEXT NOT NULL,
                created_by   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                UNIQUE(workspace_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_ws_groups_ws ON workspace_groups(workspace_id);
            CREATE TABLE IF NOT EXISTS workspace_group_members (
                group_id     TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                username     TEXT NOT NULL,
                joined_at    TEXT NOT NULL,
                PRIMARY KEY (group_id, username)
            );
            CREATE INDEX IF NOT EXISTS idx_wsgm_user ON workspace_group_members(username);
            CREATE TABLE IF NOT EXISTS resource_groups (
                resource_type TEXT NOT NULL,
                resource_id   TEXT NOT NULL,
                group_id      TEXT NOT NULL,
                shared_by     TEXT NOT NULL,
                shared_at     TEXT NOT NULL,
                PRIMARY KEY (resource_type, resource_id, group_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rg_group    ON resource_groups(group_id, resource_type);
            CREATE INDEX IF NOT EXISTS idx_rg_resource ON resource_groups(resource_type, resource_id);
        """)
        conn.commit()

    # 11. Social profile fields + follow + stars
    existing_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for col, definition in [
        ("avatar", "TEXT"),
        ("bio", "TEXT"),
        ("languages", "TEXT NOT NULL DEFAULT '[]'"),
        ("email_public", "TEXT"),
        ("github", "TEXT"),
        ("cv", "TEXT"),
    ]:
        if col not in user_cols:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                conn.commit()
            except Exception as exc:
                flog.warning(f"[db] No se pudo añadir columna {col}: {exc}")

    if "user_follows" not in existing_tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_follows (
                follower    TEXT NOT NULL,
                following   TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (follower, following)
            );
            CREATE INDEX IF NOT EXISTS idx_uf_follower  ON user_follows(follower);
            CREATE INDEX IF NOT EXISTS idx_uf_following ON user_follows(following);
        """)
        conn.commit()

    if "resource_stars" not in existing_tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS resource_stars (
                username      TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id   TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (username, resource_type, resource_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rs_resource ON resource_stars(resource_type, resource_id);
        """)
        conn.commit()

    # 12. Resource social catalog
    if "resource_social" not in existing_tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS resource_social (
                resource_type      TEXT NOT NULL,
                resource_id        TEXT NOT NULL,
                owner              TEXT NOT NULL,
                name               TEXT NOT NULL DEFAULT '',
                description        TEXT NOT NULL DEFAULT '',
                is_public          INTEGER NOT NULL DEFAULT 0,
                category           TEXT NOT NULL DEFAULT 'Other',
                trial_missing_deps TEXT NOT NULL DEFAULT 'warn',
                fork_of_user       TEXT,
                fork_of_id         TEXT,
                linked_to_user     TEXT,
                linked_to_id       TEXT,
                stars_count        INTEGER NOT NULL DEFAULT 0,
                updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (resource_type, resource_id, owner)
            );
            CREATE INDEX IF NOT EXISTS idx_rsoc_public ON resource_social(is_public, resource_type, category);
            CREATE INDEX IF NOT EXISTS idx_rsoc_owner  ON resource_social(owner, resource_type);
        """)
        conn.commit()


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
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TEXT")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_token TEXT")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                created_by  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS team_members (
                team_id     TEXT NOT NULL,
                username    TEXT NOT NULL,
                is_manager  SMALLINT NOT NULL DEFAULT 0,
                permissions TEXT NOT NULL DEFAULT '{}',
                joined_at   TEXT NOT NULL,
                PRIMARY KEY (team_id, username)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_team_members_username ON team_members(username)"
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS team_invitations (
                id              TEXT PRIMARY KEY,
                team_id         TEXT NOT NULL,
                invited_email   TEXT NOT NULL,
                invited_by      TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                expires_at      TEXT NOT NULL,
                created_at      TEXT NOT NULL
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_team_inv_email"
            " ON team_invitations(invited_email, status)"
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resource_teams (
                resource_type TEXT NOT NULL,
                resource_id   TEXT NOT NULL,
                team_id       TEXT NOT NULL,
                shared_by     TEXT NOT NULL,
                shared_at     TEXT NOT NULL,
                PRIMARY KEY (resource_type, resource_id, team_id)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_rt_team ON resource_teams(team_id, resource_type)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_rt_resource ON resource_teams(resource_type, resource_id)"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_name_creator ON teams(name, created_by)"
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                created_by  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workspace_members (
                workspace_id TEXT NOT NULL,
                username     TEXT NOT NULL,
                role         TEXT NOT NULL DEFAULT 'member',
                joined_at    TEXT NOT NULL,
                PRIMARY KEY (workspace_id, username)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(username)"
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_daily (
                day      TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                tokens   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, owner_id)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_token_daily_owner ON token_daily(owner_id, day DESC)"
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workspace_invitations (
                id           TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                invited_by   TEXT NOT NULL,
                username     TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL,
                UNIQUE(workspace_id, username)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ws_inv_user ON workspace_invitations(username, status)"
        )
        # 10. Migrate teams → workspace_groups
        cur.execute("DROP TABLE IF EXISTS resource_teams CASCADE")
        cur.execute("DROP TABLE IF EXISTS team_invitations CASCADE")
        cur.execute("DROP TABLE IF EXISTS team_members CASCADE")
        cur.execute("DROP TABLE IF EXISTS teams CASCADE")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workspace_groups (
                id           TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name         TEXT NOT NULL,
                created_by   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                UNIQUE(workspace_id, name)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ws_groups_ws ON workspace_groups(workspace_id)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workspace_group_members (
                group_id     TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                username     TEXT NOT NULL,
                joined_at    TEXT NOT NULL,
                PRIMARY KEY (group_id, username)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wsgm_user ON workspace_group_members(username)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resource_groups (
                resource_type TEXT NOT NULL,
                resource_id   TEXT NOT NULL,
                group_id      TEXT NOT NULL,
                shared_by     TEXT NOT NULL,
                shared_at     TEXT NOT NULL,
                PRIMARY KEY (resource_type, resource_id, group_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rg_group ON resource_groups(group_id, resource_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rg_resource ON resource_groups(resource_type, resource_id)")
        # 11. Social profile fields + follow + stars
        for col, definition in [
            ("avatar", "TEXT"),
            ("bio", "TEXT"),
            ("languages", "TEXT NOT NULL DEFAULT '[]'"),
            ("email_public", "TEXT"),
            ("github", "TEXT"),
            ("cv", "TEXT"),
        ]:
            cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {definition}")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_follows (
                follower    TEXT NOT NULL,
                following   TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT NOW(),
                PRIMARY KEY (follower, following)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_uf_follower  ON user_follows(follower)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_uf_following ON user_follows(following)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resource_stars (
                username      TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id   TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT NOW(),
                PRIMARY KEY (username, resource_type, resource_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rs_resource ON resource_stars(resource_type, resource_id)")
        # 12. Resource social catalog
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resource_social (
                resource_type      TEXT NOT NULL,
                resource_id        TEXT NOT NULL,
                owner              TEXT NOT NULL,
                name               TEXT NOT NULL DEFAULT '',
                description        TEXT NOT NULL DEFAULT '',
                is_public          BOOLEAN NOT NULL DEFAULT FALSE,
                category           TEXT NOT NULL DEFAULT 'Other',
                trial_missing_deps TEXT NOT NULL DEFAULT 'warn',
                fork_of_user       TEXT,
                fork_of_id         TEXT,
                linked_to_user     TEXT,
                linked_to_id       TEXT,
                stars_count        INTEGER NOT NULL DEFAULT 0,
                updated_at         TIMESTAMP WITH TIME ZONE DEFAULT now(),
                PRIMARY KEY (resource_type, resource_id, owner)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rsoc_public ON resource_social(is_public, resource_type, category)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rsoc_owner ON resource_social(owner, resource_type)")
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
