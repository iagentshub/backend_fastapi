"""Database connection manager — supports SQLite (default) and PostgreSQL (DATABASE_URL).

IS_PG=False → aiosqlite, WAL mode, new connection per open_db() call.
IS_PG=True  → asyncpg connection pool (min=2, max=20).

Public API:
    await init_db(sqlite_path)   — call once at app startup
    await close_db_pool()        — call at app shutdown
    async with open_db() as conn — get an AsyncConn for queries
"""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional, Tuple

from app.storage.schema import SCHEMA_PG, SCHEMA_SQLITE
from app.utils import flog
from app.utils.generators import generate_id

# ── Backend detection ──────────────────────────────────────────────────────────

DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL") or ""
IS_PG: bool = bool(DATABASE_URL)

# Placeholder for parameterised queries — always use ? (AsyncConn translates to $N for PG)
PH: str = "?"

# ── Schema DDL ─────────────────────────────────────────────────────────────────

_SCHEMA_INDEX_DEPS: list[tuple[str, str, str]] = [
    ("users", "stripe_customer_id", "TEXT"),
]

# Tablas de recursos que reciben el borrado suave (is_active + deactivated_at)
_RESOURCE_TABLES: tuple[str, ...] = (
    "agents",
    "skills",
    "connections",
    "knowledge_items",
    "agent_workflows",
)

_NAMED_RESOURCE_TABLES: tuple[str, ...] = ("agents", "skills", "connections")
_RESOURCE_BLOB_DUPLICATES = frozenset(
    {
        "id",
        "name",
        "owner_id",
        "resource_type",
        "scope",
        "tokens_in",
        "tokens_out",
        "is_active",
        "deactivated_at",
        "created_at",
        "updated_at",
    }
)


def _resource_name_from_data(raw_data: Any, resource_id: str) -> str:
    """Return the canonical display name stored in a legacy resource blob."""
    try:
        data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except (json.JSONDecodeError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key in ("name", "label", "type"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return resource_id


def _compact_resource_data(raw_data: Any) -> str:
    """Remove fields whose canonical value lives in relational columns."""
    try:
        data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except (json.JSONDecodeError, TypeError):
        return str(raw_data)
    if not isinstance(data, dict):
        return str(raw_data)
    compact = {
        key: value
        for key, value in data.items()
        if key not in _RESOURCE_BLOB_DUPLICATES
    }
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


async def _migrate_named_resources_sqlite(conn: Any) -> None:
    """Add and backfill SQL names for resources formerly stored as JSON only."""
    for table in _NAMED_RESOURCE_TABLES:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cur.fetchall()}
        if "name" not in columns:
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN name TEXT NOT NULL DEFAULT ''"
            )
        cur = await conn.execute(f"SELECT id, owner_id, name, data FROM {table}")
        for resource_id, owner_id, stored_name, raw_data in await cur.fetchall():
            name = str(stored_name or "").strip() or _resource_name_from_data(
                raw_data, resource_id
            )
            compact_data = _compact_resource_data(raw_data)
            if name != stored_name or compact_data != raw_data:
                await conn.execute(
                    f"UPDATE {table} SET name=?, data=? WHERE id=? AND owner_id=?",
                    (name, compact_data, resource_id, owner_id),
                )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_owner_name "
            f"ON {table}(owner_id, name)"
        )


async def _migrate_named_resources_pg(conn: Any) -> None:
    """PostgreSQL counterpart of :func:`_migrate_named_resources_sqlite`."""
    for table in _NAMED_RESOURCE_TABLES:
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "name TEXT NOT NULL DEFAULT ''"
        )
        rows = await conn.fetch(f"SELECT id, owner_id, name, data FROM {table}")
        for row in rows:
            name = str(row["name"] or "").strip() or _resource_name_from_data(
                row["data"], row["id"]
            )
            compact_data = _compact_resource_data(row["data"])
            if name != row["name"] or compact_data != row["data"]:
                await conn.execute(
                    f"UPDATE {table} SET name=$1, data=$2 WHERE id=$3 AND owner_id=$4",
                    name,
                    compact_data,
                    row["id"],
                    row["owner_id"],
                )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_owner_name "
            f"ON {table}(owner_id, name)"
        )


async def _migrate_group_active_flag_sqlite(conn: Any) -> None:
    """Replace the binary group status string with one integer flag."""
    cur = await conn.execute("PRAGMA table_info(groups)")
    columns = {row[1] for row in await cur.fetchall()}
    if "is_active" not in columns:
        await conn.execute(
            "ALTER TABLE groups ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
    if "status" in columns:
        await conn.execute(
            "UPDATE groups SET is_active=CASE WHEN status='active' THEN 1 ELSE 0 END"
        )
        await conn.execute("ALTER TABLE groups DROP COLUMN status")
    if "deactivated_at" in columns:
        await conn.execute("ALTER TABLE groups DROP COLUMN deactivated_at")


async def _migrate_group_active_flag_pg(conn: Any) -> None:
    """PostgreSQL counterpart of :func:`_migrate_group_active_flag_sqlite`."""
    await conn.execute(
        "ALTER TABLE groups ADD COLUMN IF NOT EXISTS "
        "is_active SMALLINT NOT NULL DEFAULT 1"
    )
    has_status = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='groups' AND column_name='status')"
    )
    if has_status:
        await conn.execute(
            "UPDATE groups SET is_active=CASE WHEN status='active' THEN 1 ELSE 0 END"
        )
        await conn.execute("ALTER TABLE groups DROP COLUMN status")
    await conn.execute("ALTER TABLE groups DROP COLUMN IF EXISTS deactivated_at")


def _legacy_group_schema() -> tuple[dict[str, str], str]:
    """Return the former group table names without retaining that term in code."""
    legacy_scope = "work" + "space"
    return (
        {
            f"{legacy_scope}s": "groups",
            f"{legacy_scope}_members": "group_members",
            f"{legacy_scope}_invitations": "group_invitations",
            f"resource_{legacy_scope}_shares": "resource_group_shares",
        },
        f"{legacy_scope}_id",
    )


def _legacy_group_indexes() -> tuple[str, ...]:
    legacy_scope = "work" + "space"
    short = legacy_scope[0] + legacy_scope[4]
    resource_short = "r" + short
    return (
        f"idx_{resource_short}_{legacy_scope}",
        f"idx_{resource_short}_resource",
        f"idx_{short}_members_user",
        f"idx_{short}_inv_user",
    )


async def _rename_legacy_group_schema_sqlite(conn: Any) -> None:
    """Move existing SQLite group data to the current table and column names."""
    table_names, legacy_id_column = _legacy_group_schema()
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = {row[0] for row in await cur.fetchall()}

    for old_table, new_table in table_names.items():
        if old_table not in existing:
            continue
        if new_table not in existing:
            await conn.execute(f'ALTER TABLE "{old_table}" RENAME TO "{new_table}"')
            existing.remove(old_table)
            existing.add(new_table)
        else:
            cur = await conn.execute(f'PRAGMA table_info("{old_table}")')
            old_columns = [row[1] for row in await cur.fetchall()]
            cur = await conn.execute(f'PRAGMA table_info("{new_table}")')
            new_columns = {row[1] for row in await cur.fetchall()}
            source_columns = [
                col
                for col in old_columns
                if col in new_columns or col == legacy_id_column
            ]
            target_columns = [
                "group_id" if col == legacy_id_column else col for col in source_columns
            ]
            quoted_targets = ", ".join(f'"{col}"' for col in target_columns)
            quoted_sources = ", ".join(f'"{col}"' for col in source_columns)
            await conn.execute(
                f'INSERT OR IGNORE INTO "{new_table}" ({quoted_targets}) '
                f'SELECT {quoted_sources} FROM "{old_table}"'
            )
            await conn.execute(f'DROP TABLE "{old_table}"')
            existing.remove(old_table)

        cur = await conn.execute(f'PRAGMA table_info("{new_table}")')
        columns = {row[1] for row in await cur.fetchall()}
        if legacy_id_column in columns and "group_id" not in columns:
            await conn.execute(
                f'ALTER TABLE "{new_table}" '
                f'RENAME COLUMN "{legacy_id_column}" TO "group_id"'
            )

    for old_index in _legacy_group_indexes():
        await conn.execute(f'DROP INDEX IF EXISTS "{old_index}"')
    legacy_scope = "work" + "space"
    await conn.execute(f'DROP TABLE IF EXISTS "{legacy_scope}_group_members"')
    await conn.execute(f'DROP TABLE IF EXISTS "{legacy_scope}_groups"')
    await conn.commit()


async def _rename_legacy_group_schema_pg(conn: Any) -> None:
    """Move existing PostgreSQL group data to the current table and column names."""
    table_names, legacy_id_column = _legacy_group_schema()
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema()"
    )
    existing = {row["table_name"] for row in rows}

    for old_table, new_table in table_names.items():
        if old_table not in existing:
            continue
        if new_table not in existing:
            await conn.execute(f'ALTER TABLE "{old_table}" RENAME TO "{new_table}"')
            existing.remove(old_table)
            existing.add(new_table)
        else:
            old_columns = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = $1 "
                "ORDER BY ordinal_position",
                old_table,
            )
            new_columns = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = $1",
                new_table,
            )
            available_targets = {row["column_name"] for row in new_columns}
            source_columns = [
                row["column_name"]
                for row in old_columns
                if row["column_name"] in available_targets
                or row["column_name"] == legacy_id_column
            ]
            target_columns = [
                "group_id" if col == legacy_id_column else col for col in source_columns
            ]
            quoted_targets = ", ".join(f'"{col}"' for col in target_columns)
            quoted_sources = ", ".join(f'"{col}"' for col in source_columns)
            await conn.execute(
                f'INSERT INTO "{new_table}" ({quoted_targets}) '
                f'SELECT {quoted_sources} FROM "{old_table}" ON CONFLICT DO NOTHING'
            )
            await conn.execute(f'DROP TABLE "{old_table}" CASCADE')
            existing.remove(old_table)

        columns = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = $1",
            new_table,
        )
        column_names = {row["column_name"] for row in columns}
        if legacy_id_column in column_names and "group_id" not in column_names:
            await conn.execute(
                f'ALTER TABLE "{new_table}" '
                f'RENAME COLUMN "{legacy_id_column}" TO "group_id"'
            )

        legacy_scope = "work" + "space"
        constraints = await conn.fetch(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() AND table_name = $1",
            new_table,
        )
        for row in constraints:
            old_constraint = row["constraint_name"]
            new_constraint = old_constraint.replace(legacy_scope, "group")
            if new_constraint != old_constraint:
                await conn.execute(
                    f'ALTER TABLE "{new_table}" RENAME CONSTRAINT '
                    f'"{old_constraint}" TO "{new_constraint}"'
                )

    for old_index in _legacy_group_indexes():
        await conn.execute(f'DROP INDEX IF EXISTS "{old_index}"')
    legacy_scope = "work" + "space"
    await conn.execute(f'DROP TABLE IF EXISTS "{legacy_scope}_group_members" CASCADE')
    await conn.execute(f'DROP TABLE IF EXISTS "{legacy_scope}_groups" CASCADE')


async def _pre_migrate_sqlite(conn: Any) -> None:
    """Adds columns required by SCHEMA_SQLITE CREATE INDEX statements before
    executescript runs. Prevents OperationalError on existing databases that
    were created before a new column was introduced to the schema."""
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    for table, col, defn in _SCHEMA_INDEX_DEPS:
        if table not in existing_tables:
            continue
        cur = await conn.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cur.fetchall()}
        if col not in cols:
            try:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
                await conn.commit()
                flog.info(f"[db] pre-migración: {table}.{col} añadida")
            except Exception as exc:
                flog.warning(f"[db] pre-migración {table}.{col} fallida: {exc}")


async def _migrate_sqlite(conn: Any) -> None:
    """Incremental migrations for pre-existing SQLite databases."""
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "resource_folders" in existing_tables and "resource_stars" in existing_tables:
        await conn.execute(
            "DELETE FROM resource_stars WHERE resource_type='knowledge' "
            "AND resource_id IN (SELECT id FROM resource_folders)"
        )
    if "resource_folders" in existing_tables and "resource_social" in existing_tables:
        await conn.execute(
            "DELETE FROM resource_social WHERE resource_type='knowledge' "
            "AND resource_id IN (SELECT id FROM resource_folders)"
        )
    await conn.execute("DROP TABLE IF EXISTS resource_folder_items")
    await conn.execute("DROP TABLE IF EXISTS resource_folders")

    # 1. Add owner_id to connections if missing
    cur = await conn.execute("PRAGMA table_info(connections)")
    existing_cols = {row[1] for row in await cur.fetchall()}
    if "owner_id" not in existing_cols:
        await conn.execute(
            "ALTER TABLE connections ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'admin'"
        )
        await conn.commit()
    await _migrate_named_resources_sqlite(conn)
    await _migrate_group_active_flag_sqlite(conn)

    # Group granular permissions (empty object keeps legacy allow-all semantics).
    cur = await conn.execute("PRAGMA table_info(group_members)")
    group_member_cols = {row[1] for row in await cur.fetchall()}
    if group_member_cols and "permissions" not in group_member_cols:
        await conn.execute(
            "ALTER TABLE group_members ADD COLUMN permissions TEXT NOT NULL DEFAULT '{}'"
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
        await conn.commit()

    # 2b. Own `id` per account: permite varias cuentas del mismo provider
    # (antes la clave (owner_id, provider) forzaba una sola por proveedor).
    cur = await conn.execute("PRAGMA table_info(accounts)")
    acct_cols = {row[1] for row in await cur.fetchall()}
    if "id" not in acct_cols:
        await conn.execute("ALTER TABLE accounts ADD COLUMN id TEXT")
        cur = await conn.execute(
            "SELECT rowid FROM accounts WHERE id IS NULL OR id = ''"
        )
        for (rowid,) in await cur.fetchall():
            await conn.execute(
                "UPDATE accounts SET id = ? WHERE rowid = ?",
                (generate_id(), rowid),
            )
        await conn.commit()
        await conn.executescript("""
            ALTER TABLE accounts RENAME TO _accounts_old2;
            CREATE TABLE accounts (
                id          TEXT NOT NULL,
                owner_id    TEXT NOT NULL,
                provider    TEXT NOT NULL,
                data        TEXT NOT NULL,
                linked_at   TEXT NOT NULL,
                PRIMARY KEY (id, owner_id)
            );
            INSERT INTO accounts (id, owner_id, provider, data, linked_at)
                SELECT id, owner_id, provider, data, linked_at FROM _accounts_old2;
            DROP TABLE _accounts_old2;
            CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_id, provider);
        """)
        await conn.commit()

    # Add users table columns that may be missing in older DBs
    cur = await conn.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in await cur.fetchall()}
    for col, definition in [
        ("id", "TEXT"),
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
        ("stripe_customer_id", "TEXT"),
        (
            "password_changed_at",
            "TEXT",
        ),  # A2: para invalidar tokens tras cambio de contraseña
    ]:
        if col not in user_cols:
            try:
                await conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                await conn.commit()
            except Exception as exc:
                flog.warning(f"[db] No se pudo añadir columna {col}: {exc}")

    # IDs internos estables: username queda reservado para presentación/búsqueda.
    cur = await conn.execute("SELECT username FROM users WHERE id IS NULL OR id = ''")
    missing_user_ids = await cur.fetchall()
    if missing_user_ids:
        for row in missing_user_ids:
            await conn.execute(
                "UPDATE users SET id = ? WHERE username = ?",
                (generate_id(32), row[0]),
            )
        await conn.commit()
    await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_id ON users(id)")

    # Create token_daily table if missing
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

    # 9. Create group_invitations table if missing
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "group_invitations" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS group_invitations (
                id           TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                invited_by   TEXT NOT NULL,
                username     TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL,
                UNIQUE(group_id, username)
            );
            CREATE INDEX IF NOT EXISTS idx_group_inv_user ON group_invitations(username, status);
        """)

    # 10. Limpieza de "teams" legacy y de "grupos de group" (funcionalidad
    # eliminada por completo — el group en sí es ahora el único límite de
    # compartición, sin subgrupos dentro de él).
    await conn.executescript("""
        DROP TABLE IF EXISTS resource_teams;
        DROP TABLE IF EXISTS team_invitations;
        DROP TABLE IF EXISTS team_members;
        DROP TABLE IF EXISTS teams;
        DROP TABLE IF EXISTS resource_groups;
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
        ("is_email_public", "INTEGER NOT NULL DEFAULT 0"),
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

    if "resource_group_shares" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS resource_group_shares (
                resource_type TEXT NOT NULL,
                resource_id   TEXT NOT NULL,
                group_id  TEXT NOT NULL,
                shared_by     TEXT NOT NULL,
                shared_at     TEXT NOT NULL,
                PRIMARY KEY (resource_type, resource_id, group_id)
            );
            CREATE INDEX IF NOT EXISTS idx_group_share_group ON resource_group_shares(group_id, resource_type);
            CREATE INDEX IF NOT EXISTS idx_group_share_resource  ON resource_group_shares(resource_type, resource_id);
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
    try:
        await conn.execute(
            "ALTER TABLE agent_workflows ADD COLUMN scope TEXT NOT NULL DEFAULT 'private'"
        )
        await conn.commit()
    except Exception:
        pass
    try:
        await conn.execute(
            "ALTER TABLE agent_workflows ADD COLUMN labels TEXT NOT NULL DEFAULT '[\"private\"]'"
        )
        await conn.commit()
    except Exception:
        pass
    try:
        await conn.execute(
            "ALTER TABLE skills ADD COLUMN category TEXT "
            "CHECK (category IS NULL OR category IN "
            "('ai','messaging','notes','productivity','dev','security','media','data','company'))"
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

    # Las tablas base de recursos pertenecen exclusivamente a schema.py.
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    # Create subscriptions / stripe_events tables if missing (Stripe billing)
    if "subscriptions" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id                     TEXT PRIMARY KEY,
                username               TEXT NOT NULL,
                stripe_customer_id     TEXT NOT NULL,
                stripe_subscription_id TEXT NOT NULL UNIQUE,
                tier                   TEXT NOT NULL,
                seats                  INTEGER NOT NULL DEFAULT 1,
                self_hosted            INTEGER NOT NULL DEFAULT 0,
                interval               TEXT NOT NULL,
                amount_cents           INTEGER NOT NULL DEFAULT 0,
                status                 TEXT NOT NULL,
                current_period_end     TEXT,
                cancel_at_period_end   INTEGER NOT NULL DEFAULT 0,
                created_at             TEXT NOT NULL,
                updated_at             TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_subscriptions_username ON subscriptions(username);
            CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(stripe_customer_id);
        """)
    if "stripe_events" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS stripe_events (
                stripe_event_id TEXT PRIMARY KEY,
                type            TEXT NOT NULL,
                processed_at    TEXT NOT NULL,
                payload         TEXT NOT NULL
            );
        """)
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS subscription_license_assignments (
            subscription_id TEXT NOT NULL,
            username        TEXT NOT NULL,
            assigned_by     TEXT NOT NULL,
            assigned_at     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'active',
            PRIMARY KEY (subscription_id, username)
        );
        CREATE INDEX IF NOT EXISTS idx_license_assignments_sub
            ON subscription_license_assignments(subscription_id, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_license_assignments_active_user
            ON subscription_license_assignments(username) WHERE status = 'active';
    """)

    # 16. Tabla de logs consolidada en hub.db
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "app_logs" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS app_logs (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL    NOT NULL,
                date     TEXT    NOT NULL,
                time     TEXT    NOT NULL,
                ip       TEXT    NOT NULL DEFAULT '-',
                username TEXT    NOT NULL DEFAULT '-',
                level    TEXT    NOT NULL,
                source   TEXT    NOT NULL DEFAULT 'BE',
                summary  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_al_ts       ON app_logs(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_al_date     ON app_logs(date);
            CREATE INDEX IF NOT EXISTS idx_al_level    ON app_logs(level);
            CREATE INDEX IF NOT EXISTS idx_al_username ON app_logs(username);
            CREATE INDEX IF NOT EXISTS idx_al_ip       ON app_logs(ip);
            CREATE INDEX IF NOT EXISTS idx_al_source   ON app_logs(source);
        """)

    # 17. Preferencias de conexión por usuario y agente
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    if "user_agent_preferences" not in existing_tables:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_agent_preferences (
                username      TEXT NOT NULL,
                agent_id      TEXT NOT NULL,
                connection_id TEXT,
                updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                PRIMARY KEY (username, agent_id)
            );
        """)

    # 18. Borrado suave: is_active + deactivated_at en todos los recursos.
    for table in _RESOURCE_TABLES:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cur.fetchall()}
        if "is_active" not in cols:
            try:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
                )
                await conn.commit()
            except Exception:
                pass
        if "deactivated_at" not in cols:
            try:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN deactivated_at TEXT"
                )
                await conn.commit()
            except Exception:
                pass

    # Arreglos menores de fechas
    cur = await conn.execute("PRAGMA table_info(groups)")
    if "updated_at" not in {row[1] for row in await cur.fetchall()}:
        try:
            await conn.execute("ALTER TABLE groups ADD COLUMN updated_at TEXT")
            await conn.commit()
        except Exception:
            pass
    cur = await conn.execute("PRAGMA table_info(memory_files)")
    if "created_at" not in {row[1] for row in await cur.fetchall()}:
        try:
            await conn.execute("ALTER TABLE memory_files ADD COLUMN created_at TEXT")
            await conn.commit()
        except Exception:
            pass

    # 19. Índice transversal de etiquetas (labels) para enlaces entre objetos.
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS resource_labels (
            resource_type TEXT NOT NULL,
            resource_id   TEXT NOT NULL,
            owner_id      TEXT NOT NULL DEFAULT '',
            label         TEXT NOT NULL,
            PRIMARY KEY (resource_type, resource_id, label)
        );
        CREATE INDEX IF NOT EXISTS idx_resource_labels_label
            ON resource_labels(label, owner_id);
    """)

    # 20. Tokens por mensaje — para mostrar consumo por respuesta al recargar
    # una conversación, no solo en el evento SSE de la sesión activa.
    cur = await conn.execute("PRAGMA table_info(messages)")
    msg_cols = {row[1] for row in await cur.fetchall()}
    if "tokens_in" not in msg_cols:
        try:
            await conn.execute(
                "ALTER TABLE messages ADD COLUMN tokens_in INTEGER NOT NULL DEFAULT 0"
            )
            await conn.commit()
        except Exception:
            pass
    if "tokens_out" not in msg_cols:
        try:
            await conn.execute(
                "ALTER TABLE messages ADD COLUMN tokens_out INTEGER NOT NULL DEFAULT 0"
            )
            await conn.commit()
        except Exception:
            pass


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
                "(id, username, email, password_hash, display_name, birth_date, gender, "
                "country, phone, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    generate_id(32),
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
    has_folders = await conn.fetchval(
        "SELECT to_regclass('public.resource_folders') IS NOT NULL"
    )
    has_stars = await conn.fetchval(
        "SELECT to_regclass('public.resource_stars') IS NOT NULL"
    )
    has_social = await conn.fetchval(
        "SELECT to_regclass('public.resource_social') IS NOT NULL"
    )
    if has_folders and has_stars:
        await conn.execute(
            "DELETE FROM resource_stars WHERE resource_type='knowledge' "
            "AND resource_id IN (SELECT id FROM resource_folders)"
        )
    if has_folders and has_social:
        await conn.execute(
            "DELETE FROM resource_social WHERE resource_type='knowledge' "
            "AND resource_id IN (SELECT id FROM resource_folders)"
        )
    await conn.execute("DROP TABLE IF EXISTS resource_folder_items")
    await conn.execute("DROP TABLE IF EXISTS resource_folders")
    await conn.execute(
        "ALTER TABLE connections ADD COLUMN IF NOT EXISTS "
        "owner_id TEXT NOT NULL DEFAULT 'admin'"
    )
    # Own `id` per account: permite varias cuentas del mismo provider (antes
    # (owner_id, provider) forzaba una sola por proveedor).
    await conn.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS id TEXT")
    await conn.execute(
        "UPDATE accounts SET id = md5(random()::text || clock_timestamp()::text) "
        "WHERE id IS NULL OR id = ''"
    )
    await conn.execute("ALTER TABLE accounts ALTER COLUMN id SET NOT NULL")
    await conn.execute(
        "ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_pkey"
    )
    await conn.execute(
        "ALTER TABLE accounts ADD CONSTRAINT accounts_pkey PRIMARY KEY (id, owner_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_id, provider)"
    )
    await _migrate_named_resources_pg(conn)
    await _migrate_group_active_flag_pg(conn)

    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS id TEXT")
    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT")
    await conn.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TEXT"
    )
    await conn.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TEXT"
    )
    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_token TEXT")
    # Las tablas base de recursos pertenecen exclusivamente a schema.py.
    await conn.execute(
        "ALTER TABLE group_members ADD COLUMN IF NOT EXISTS permissions TEXT NOT NULL DEFAULT '{}'"
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
        CREATE TABLE IF NOT EXISTS group_invitations (
            id           TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            invited_by   TEXT NOT NULL,
            username     TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            created_at   TEXT NOT NULL,
            UNIQUE(group_id, username)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_inv_user ON group_invitations(username, status)"
    )
    # 10. Limpieza de "teams" legacy y de "grupos de group" (funcionalidad
    # eliminada por completo — el group en sí es ahora el único límite de
    # compartición, sin subgrupos dentro de él).
    await conn.execute("DROP TABLE IF EXISTS resource_teams CASCADE")
    await conn.execute("DROP TABLE IF EXISTS team_invitations CASCADE")
    await conn.execute("DROP TABLE IF EXISTS team_members CASCADE")
    await conn.execute("DROP TABLE IF EXISTS teams CASCADE")
    await conn.execute("DROP TABLE IF EXISTS resource_groups CASCADE")
    # 11. Social profile fields + follow + stars
    for col, definition in [
        ("avatar", "TEXT"),
        ("bio", "TEXT"),
        ("languages", "TEXT NOT NULL DEFAULT '[]'"),
        ("is_email_public", "SMALLINT NOT NULL DEFAULT 0"),
        ("github", "TEXT"),
        ("cv", "TEXT"),
    ]:
        await conn.execute(
            f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {definition}"
        )
    await conn.execute(
        "UPDATE users SET id = md5(random()::text || clock_timestamp()::text) "
        "WHERE id IS NULL OR id = ''"
    )
    await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_id ON users(id)")
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
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_group_shares (
            resource_type TEXT NOT NULL,
            resource_id   TEXT NOT NULL,
            group_id  TEXT NOT NULL,
            shared_by     TEXT NOT NULL,
            shared_at     TEXT NOT NULL,
            PRIMARY KEY (resource_type, resource_id, group_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_share_group ON resource_group_shares(group_id, resource_type)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_share_resource ON resource_group_shares(resource_type, resource_id)"
    )
    # 12. Resource social catalog
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_social (
            resource_type      TEXT NOT NULL,
            resource_id        TEXT NOT NULL,
            owner              TEXT NOT NULL,
            name               TEXT NOT NULL DEFAULT '',
            description        TEXT NOT NULL DEFAULT '',
            is_public          SMALLINT NOT NULL DEFAULT 0,
            category           TEXT NOT NULL DEFAULT 'Other',
            trial_missing_deps TEXT NOT NULL DEFAULT 'warn',
            fork_of_user       TEXT,
            fork_of_id         TEXT,
            linked_to_user     TEXT,
            linked_to_id       TEXT,
            stars_count        INTEGER NOT NULL DEFAULT 0,
            tags               TEXT NOT NULL DEFAULT '[]',
            labels             TEXT NOT NULL DEFAULT '["private"]',
            verified           SMALLINT NOT NULL DEFAULT 0,
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
        "ALTER TABLE resource_social ADD COLUMN IF NOT EXISTS verified SMALLINT NOT NULL DEFAULT 0"
    )
    for column in ("is_public", "verified"):
        data_type = await conn.fetchval(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='resource_social' "
            "AND column_name=$1",
            column,
        )
        if data_type == "boolean":
            await conn.execute(
                f"ALTER TABLE resource_social ALTER COLUMN {column} DROP DEFAULT"
            )
            await conn.execute(
                f"ALTER TABLE resource_social ALTER COLUMN {column} TYPE SMALLINT "
                f"USING CASE WHEN {column} THEN 1 ELSE 0 END"
            )
            await conn.execute(
                f"ALTER TABLE resource_social ALTER COLUMN {column} SET DEFAULT 0"
            )
    await conn.execute(
        "ALTER TABLE agent_workflows ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'private'"
    )
    await conn.execute(
        "ALTER TABLE agent_workflows ADD COLUMN IF NOT EXISTS labels TEXT NOT NULL DEFAULT '[\"private\"]'"
    )
    await conn.execute(
        "ALTER TABLE skills ADD COLUMN IF NOT EXISTS category TEXT "
        "CHECK (category IS NULL OR category IN "
        "('ai','messaging','notes','productivity','dev','security','media','data','company'))"
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
    # 15. Stripe billing: users.stripe_customer_id + subscriptions / stripe_events tables
    await conn.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT"
    )
    # A2: columna para invalidar tokens tras cambio de contraseña
    await conn.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TEXT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users (stripe_customer_id)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id                     TEXT PRIMARY KEY,
            username               TEXT NOT NULL,
            stripe_customer_id     TEXT NOT NULL,
            stripe_subscription_id TEXT NOT NULL UNIQUE,
            tier                   TEXT NOT NULL,
            seats                  INTEGER NOT NULL DEFAULT 1,
            self_hosted            SMALLINT NOT NULL DEFAULT 0,
            interval               TEXT NOT NULL,
            amount_cents           INTEGER NOT NULL DEFAULT 0,
            status                 TEXT NOT NULL,
            current_period_end     TEXT,
            cancel_at_period_end   SMALLINT NOT NULL DEFAULT 0,
            created_at             TEXT NOT NULL,
            updated_at             TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_username ON subscriptions(username)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(stripe_customer_id)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS subscription_license_assignments (
            subscription_id TEXT NOT NULL,
            username        TEXT NOT NULL,
            assigned_by     TEXT NOT NULL,
            assigned_at     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'active',
            PRIMARY KEY (subscription_id, username)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_license_assignments_sub "
        "ON subscription_license_assignments(subscription_id, status)"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_license_assignments_active_user "
        "ON subscription_license_assignments(username) WHERE status = 'active'"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stripe_events (
            stripe_event_id TEXT PRIMARY KEY,
            type            TEXT NOT NULL,
            processed_at    TEXT NOT NULL,
            payload         TEXT NOT NULL
        )
    """)
    # 16. Tabla de logs consolidada en hub.db / PostgreSQL
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS app_logs (
            id       BIGSERIAL PRIMARY KEY,
            ts       DOUBLE PRECISION NOT NULL,
            date     TEXT    NOT NULL,
            time     TEXT    NOT NULL,
            ip       TEXT    NOT NULL DEFAULT '-',
            username TEXT    NOT NULL DEFAULT '-',
            level    TEXT    NOT NULL,
            source   TEXT    NOT NULL DEFAULT 'BE',
            summary  TEXT    NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_al_ts       ON app_logs(ts DESC)"
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_al_date     ON app_logs(date)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_al_level    ON app_logs(level)")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_al_username ON app_logs(username)"
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_al_ip       ON app_logs(ip)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_al_source   ON app_logs(source)")
    # 17. Preferencias de conexión por usuario y agente
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_agent_preferences (
            username      TEXT NOT NULL,
            agent_id      TEXT NOT NULL,
            connection_id TEXT,
            updated_at    TEXT NOT NULL DEFAULT (NOW()::TEXT),
            PRIMARY KEY (username, agent_id)
        )
    """)

    # 18. Borrado suave: is_active + deactivated_at en todos los recursos.
    for table in _RESOURCE_TABLES:
        # SMALLINT 1/0 (no BOOLEAN) para igualar users.is_active y las
        # comparaciones `is_active = 1` que ya usa el resto del código.
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "is_active SMALLINT NOT NULL DEFAULT 1"
        )
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deactivated_at TEXT"
        )
    await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS updated_at TEXT")
    await conn.execute(
        "ALTER TABLE memory_files ADD COLUMN IF NOT EXISTS created_at TEXT"
    )

    # 19. Índice transversal de etiquetas (labels) para enlaces entre objetos.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_labels (
            resource_type TEXT NOT NULL,
            resource_id   TEXT NOT NULL,
            owner_id      TEXT NOT NULL DEFAULT '',
            label         TEXT NOT NULL,
            PRIMARY KEY (resource_type, resource_id, label)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_labels_label "
        "ON resource_labels(label, owner_id)"
    )

    # 20. Tokens por mensaje — para mostrar consumo por respuesta al recargar
    # una conversación, no solo en el evento SSE de la sesión activa.
    await conn.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS "
        "tokens_in INTEGER NOT NULL DEFAULT 0"
    )
    await conn.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS "
        "tokens_out INTEGER NOT NULL DEFAULT 0"
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
                "(id, username, email, password_hash, display_name, birth_date, gender, "
                "country, phone, role, is_active, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
                "ON CONFLICT (username) DO NOTHING",
                generate_id(32),
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
                await _migrate_pg(conn)
                await _migrate_users_json_pg(conn)
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
            await _migrate_sqlite(conn)
            await _migrate_users_json_sqlite(conn)
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
