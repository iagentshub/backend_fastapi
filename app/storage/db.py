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

from app.utils import flog

# ── Backend detection ──────────────────────────────────────────────────────────

DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL") or ""
IS_PG: bool = bool(DATABASE_URL)

# Placeholder for parameterised queries — always use ? (AsyncConn translates to $N for PG)
PH: str = "?"

# ── Schema DDL ─────────────────────────────────────────────────────────────────

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    folder_id   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS skills (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    folder_id   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS memory_files (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT 'admin',
    content     TEXT NOT NULL DEFAULT '',
    folder_id   TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory_files(owner_id, updated_at DESC);
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
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    folder_id   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS skills (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    folder_id   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS memory_files (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT 'admin',
    content     TEXT NOT NULL DEFAULT '',
    folder_id   TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory_files(owner_id, updated_at DESC);
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

# ── SQLite migrations (async) ──────────────────────────────────────────────────


async def _migrate_sqlite(conn: Any) -> None:
    """Incremental migrations for pre-existing SQLite databases."""
    # 1. Add owner_id to connections if missing
    cur = await conn.execute("PRAGMA table_info(connections)")
    existing_cols = {row[1] for row in await cur.fetchall()}
    if "owner_id" not in existing_cols:
        await conn.execute(
            "ALTER TABLE connections ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'admin'"
        )
        await conn.commit()

    # 2. Recreate accounts with composite PK if it still uses a simple PK
    cur = await conn.execute("PRAGMA table_info(accounts)")
    acct_cols = {row[1] for row in await cur.fetchall()}
    if "owner_id" not in acct_cols:
        await conn.executescript("""
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

    # 3. Add folder_id to knowledge_items if missing
    cur = await conn.execute("PRAGMA table_info(knowledge_items)")
    ki_cols = {row[1] for row in await cur.fetchall()}
    if "folder_id" not in ki_cols:
        await conn.execute("ALTER TABLE knowledge_items ADD COLUMN folder_id TEXT")
        await conn.commit()

    # 4. Create team tables if missing
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "teams" not in existing_tables:
        await conn.executescript("""
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

    # 5. Add users table columns that may be missing in older DBs
    cur = await conn.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in await cur.fetchall()}
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
                await conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                await conn.commit()
            except Exception as exc:
                flog.warning(f"[db] No se pudo añadir columna {col}: {exc}")

    # 6. Create resource_teams if missing
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "resource_teams" not in existing_tables:
        await conn.executescript("""
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

    # 7. Create workspaces tables if missing
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "workspaces" not in existing_tables:
        await conn.executescript("""
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

    try:
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_name_creator ON teams(name, created_by)"
        )
        await conn.commit()
    except Exception:
        pass

    # 8. Create token_daily table if missing
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "token_daily" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_daily (
                day      TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                tokens   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, owner_id)
            );
            CREATE INDEX IF NOT EXISTS idx_token_daily_owner ON token_daily(owner_id, day DESC);
        """)

    # 9. Create workspace_invitations table if missing
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "workspace_invitations" not in existing_tables:
        await conn.executescript("""
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

    # 10. Migrate teams → workspace_groups (drop old, create new)
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "workspace_groups" not in existing_tables:
        await conn.executescript("""
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

    # 11. Social profile fields + follow + stars
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    cur = await conn.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in await cur.fetchall()}
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
                await conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                await conn.commit()
            except Exception as exc:
                flog.warning(f"[db] No se pudo añadir columna {col}: {exc}")

    if "user_follows" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_follows (
                follower    TEXT NOT NULL,
                following   TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (follower, following)
            );
            CREATE INDEX IF NOT EXISTS idx_uf_follower  ON user_follows(follower);
            CREATE INDEX IF NOT EXISTS idx_uf_following ON user_follows(following);
        """)

    if "resource_stars" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS resource_stars (
                username      TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id   TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (username, resource_type, resource_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rs_resource ON resource_stars(resource_type, resource_id);
        """)

    # 12. Resource social catalog
    if "resource_social" not in existing_tables:
        await conn.executescript("""
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
                tags               TEXT NOT NULL DEFAULT '[]',
                labels             TEXT NOT NULL DEFAULT '["private"]',
                verified           INTEGER NOT NULL DEFAULT 0,
                updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (resource_type, resource_id, owner)
            );
            CREATE INDEX IF NOT EXISTS idx_rsoc_public ON resource_social(is_public, resource_type, category);
            CREATE INDEX IF NOT EXISTS idx_rsoc_owner  ON resource_social(owner, resource_type);
        """)
    try:
        await conn.execute(
            "ALTER TABLE resource_social ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"
        )
        await conn.commit()
    except Exception:
        pass
    try:
        await conn.execute(
            "ALTER TABLE resource_social ADD COLUMN labels TEXT NOT NULL DEFAULT '[\"private\"]'"
        )
        await conn.commit()
    except Exception:
        pass
    try:
        await conn.execute(
            "ALTER TABLE resource_social ADD COLUMN verified INTEGER NOT NULL DEFAULT 0"
        )
        await conn.commit()
    except Exception:
        pass

    # 13. Limpiar tokens guardados en plano (pre-hash). Los hasheados son siempre 64 chars hex.
    try:
        await conn.execute(
            "UPDATE users SET verification_token = NULL "
            "WHERE verification_token IS NOT NULL "
            "AND (LENGTH(verification_token) != 64 OR verification_token GLOB '*[^0-9a-f]*')"
        )
        await conn.execute(
            "UPDATE users SET deletion_token = NULL, deletion_requested_at = NULL "
            "WHERE deletion_token IS NOT NULL "
            "AND (LENGTH(deletion_token) != 64 OR deletion_token GLOB '*[^0-9a-f]*')"
        )
        await conn.execute(
            "UPDATE users SET reset_token = NULL, reset_token_expires = NULL "
            "WHERE reset_token IS NOT NULL "
            "AND (LENGTH(reset_token) != 64 OR reset_token GLOB '*[^0-9a-f]*')"
        )
        await conn.commit()
    except Exception:
        pass

    # 14. Create agents, skills, memory_files tables if missing
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "agents" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '__public__',
                scope TEXT NOT NULL DEFAULT 'private', data TEXT NOT NULL,
                tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
                folder_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (id, owner_id)
            );
            CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id, scope, updated_at DESC);
        """)
    if "skills" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '__public__',
                scope TEXT NOT NULL DEFAULT 'private', data TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '', folder_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (id, owner_id)
            );
            CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id, scope, updated_at DESC);
        """)
    if "memory_files" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_files (
                id TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT 'admin',
                content TEXT NOT NULL DEFAULT '', folder_id TEXT,
                updated_at TEXT NOT NULL, PRIMARY KEY (id, owner_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory_files(owner_id, updated_at DESC);
        """)


async def _migrate_users_json_sqlite(conn: Any) -> None:
    """Import users.json into the users table if it exists and the table is empty."""
    import json
    from pathlib import Path as _Path

    cur = await conn.execute("SELECT COUNT(*) FROM users")
    row = await cur.fetchone()
    if row and row[0]:
        return

    data_dir_env = os.environ.get("GAIA_DATA_DIR", "")
    users_json = (
        _Path(data_dir_env) / "users.json"
        if data_dir_env
        else _Path("data") / "users.json"
    )
    if not users_json.exists():
        return

    try:
        users = json.loads(users_json.read_text(encoding="utf-8"))
        from datetime import datetime, timezone

        for u in users:
            username = u.get("username", "")
            email = u.get("email") or f"{username}@migrated.local"
            await conn.execute(
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
        await conn.commit()
        users_json.rename(users_json.with_suffix(".migrated"))
    except Exception as exc:
        flog.warning(f"[db] Importación users.json (SQLite) fallida: {exc}")


# ── PostgreSQL migrations (async) ──────────────────────────────────────────────


async def _migrate_pg(conn: Any) -> None:
    """Incremental migrations for pre-existing PostgreSQL databases."""
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS folder_id TEXT"
    )
    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT")
    await conn.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TEXT"
    )
    await conn.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TEXT"
    )
    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_token TEXT")
    # agents / skills / memory_files
    await conn.execute("""CREATE TABLE IF NOT EXISTS agents (
        id TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '__public__',
        scope TEXT NOT NULL DEFAULT 'private', data TEXT NOT NULL,
        tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
        folder_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY (id, owner_id))""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id, scope, updated_at DESC)"
    )
    await conn.execute("""CREATE TABLE IF NOT EXISTS skills (
        id TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '__public__',
        scope TEXT NOT NULL DEFAULT 'private', data TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '', folder_id TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY (id, owner_id))""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id, scope, updated_at DESC)"
    )
    await conn.execute("""CREATE TABLE IF NOT EXISTS memory_files (
        id TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT 'admin',
        content TEXT NOT NULL DEFAULT '', folder_id TEXT,
        updated_at TEXT NOT NULL, PRIMARY KEY (id, owner_id))""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory_files(owner_id, updated_at DESC)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            created_by  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            team_id     TEXT NOT NULL,
            username    TEXT NOT NULL,
            is_manager  SMALLINT NOT NULL DEFAULT 0,
            permissions TEXT NOT NULL DEFAULT '{}',
            joined_at   TEXT NOT NULL,
            PRIMARY KEY (team_id, username)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_members_username ON team_members(username)"
    )
    await conn.execute("""
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
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_inv_email ON team_invitations(invited_email, status)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_teams (
            resource_type TEXT NOT NULL,
            resource_id   TEXT NOT NULL,
            team_id       TEXT NOT NULL,
            shared_by     TEXT NOT NULL,
            shared_at     TEXT NOT NULL,
            PRIMARY KEY (resource_type, resource_id, team_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rt_team ON resource_teams(team_id, resource_type)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rt_resource ON resource_teams(resource_type, resource_id)"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_name_creator ON teams(name, created_by)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            created_by  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workspace_members (
            workspace_id TEXT NOT NULL,
            username     TEXT NOT NULL,
            role         TEXT NOT NULL DEFAULT 'member',
            joined_at    TEXT NOT NULL,
            PRIMARY KEY (workspace_id, username)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(username)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS token_daily (
            day      TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            tokens   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, owner_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_token_daily_owner ON token_daily(owner_id, day DESC)"
    )
    await conn.execute("""
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
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ws_inv_user ON workspace_invitations(username, status)"
    )
    # 10. Migrate teams → workspace_groups
    await conn.execute("DROP TABLE IF EXISTS resource_teams CASCADE")
    await conn.execute("DROP TABLE IF EXISTS team_invitations CASCADE")
    await conn.execute("DROP TABLE IF EXISTS team_members CASCADE")
    await conn.execute("DROP TABLE IF EXISTS teams CASCADE")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workspace_groups (
            id           TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name         TEXT NOT NULL,
            created_by   TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            UNIQUE(workspace_id, name)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ws_groups_ws ON workspace_groups(workspace_id)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workspace_group_members (
            group_id     TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            username     TEXT NOT NULL,
            joined_at    TEXT NOT NULL,
            PRIMARY KEY (group_id, username)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wsgm_user ON workspace_group_members(username)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_groups (
            resource_type TEXT NOT NULL,
            resource_id   TEXT NOT NULL,
            group_id      TEXT NOT NULL,
            shared_by     TEXT NOT NULL,
            shared_at     TEXT NOT NULL,
            PRIMARY KEY (resource_type, resource_id, group_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rg_group ON resource_groups(group_id, resource_type)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rg_resource ON resource_groups(resource_type, resource_id)"
    )
    # 11. Social profile fields + follow + stars
    for col, definition in [
        ("avatar", "TEXT"),
        ("bio", "TEXT"),
        ("languages", "TEXT NOT NULL DEFAULT '[]'"),
        ("email_public", "TEXT"),
        ("github", "TEXT"),
        ("cv", "TEXT"),
    ]:
        await conn.execute(
            f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {definition}"
        )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_follows (
            follower    TEXT NOT NULL,
            following   TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT NOW(),
            PRIMARY KEY (follower, following)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_uf_follower ON user_follows(follower)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_uf_following ON user_follows(following)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_stars (
            username      TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id   TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT NOW(),
            PRIMARY KEY (username, resource_type, resource_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rs_resource ON resource_stars(resource_type, resource_id)"
    )
    # 12. Resource social catalog
    await conn.execute("""
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
            tags               TEXT NOT NULL DEFAULT '[]',
            labels             TEXT NOT NULL DEFAULT '["private"]',
            verified           BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at         TIMESTAMP WITH TIME ZONE DEFAULT now(),
            PRIMARY KEY (resource_type, resource_id, owner)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rsoc_public ON resource_social(is_public, resource_type, category)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rsoc_owner ON resource_social(owner, resource_type)"
    )
    await conn.execute(
        "ALTER TABLE resource_social ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT '[]'"
    )
    await conn.execute(
        "ALTER TABLE resource_social ADD COLUMN IF NOT EXISTS labels TEXT NOT NULL DEFAULT '[\"private\"]'"
    )
    await conn.execute(
        "ALTER TABLE resource_social ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # 13. Limpiar tokens de email/borrado guardados en plano (pre-hash)
    await conn.execute(
        "UPDATE users SET verification_token = NULL "
        "WHERE verification_token IS NOT NULL "
        "AND (LENGTH(verification_token) != 64 OR verification_token !~ '^[0-9a-f]+$')"
    )
    await conn.execute(
        "UPDATE users SET deletion_token = NULL, deletion_requested_at = NULL "
        "WHERE deletion_token IS NOT NULL "
        "AND (LENGTH(deletion_token) != 64 OR deletion_token !~ '^[0-9a-f]+$')"
    )
    await conn.execute(
        "UPDATE users SET reset_token = NULL, reset_token_expires = NULL "
        "WHERE reset_token IS NOT NULL "
        "AND (LENGTH(reset_token) != 64 OR reset_token !~ '^[0-9a-f]+$')"
    )


async def _migrate_users_json_pg(conn: Any) -> None:
    """Import users.json into the users table if it exists and the table is empty."""
    import json
    from pathlib import Path as _Path

    count = await conn.fetchval("SELECT COUNT(*) FROM users")
    if count:
        return

    data_dir_env = os.environ.get("GAIA_DATA_DIR", "")
    users_json = (
        _Path(data_dir_env) / "users.json"
        if data_dir_env
        else _Path("data") / "users.json"
    )
    if not users_json.exists():
        return

    try:
        users = json.loads(users_json.read_text(encoding="utf-8"))
        from datetime import datetime, timezone

        for u in users:
            username = u.get("username", "")
            email = u.get("email") or f"{username}@migrated.local"
            await conn.execute(
                "INSERT INTO users "
                "(username, email, password_hash, display_name, birth_date, gender, "
                "country, phone, role, is_active, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                "ON CONFLICT (username) DO NOTHING",
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
            )
        users_json.rename(users_json.with_suffix(".migrated"))
    except Exception as exc:
        flog.warning(f"[db] Importación users.json (PG) fallida: {exc}")


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
            yield
            await self._conn.commit()


# ── Lifecycle ──────────────────────────────────────────────────────────────────


async def init_db(sqlite_path: Optional[Path] = None) -> None:
    """Initialize DB connections and run schema migrations. Call once at startup."""
    global _pg_pool, _sqlite_path

    if IS_PG:
        import asyncpg  # type: ignore[import]

        _pg_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=20,
            command_timeout=30,
        )
        async with _pg_pool.acquire() as conn:
            async with conn.transaction():
                for stmt in _SCHEMA_PG.split(";"):
                    s = stmt.strip()
                    if s:
                        await conn.execute(s)
                await _migrate_pg(conn)
                await _migrate_users_json_pg(conn)
        flog.ok("[db] asyncpg pool iniciado")
    else:
        import aiosqlite  # type: ignore[import]
        import sqlite3

        if sqlite_path:
            _sqlite_path = sqlite_path
        if _sqlite_path is None:
            raise RuntimeError(
                "init_db() requires sqlite_path when not using PostgreSQL"
            )
        _sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(_sqlite_path)) as conn:
            conn.row_factory = sqlite3.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.executescript(_SCHEMA_SQLITE)
            await _migrate_sqlite(conn)
            await _migrate_users_json_sqlite(conn)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_connections_owner ON connections(owner_id)"
            )
            await conn.commit()
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
        import aiosqlite  # type: ignore[import]
        import sqlite3

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
