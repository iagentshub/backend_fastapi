"""La puesta al día de una instalación SQLite antigua, en un solo bloque.

**No se trocea.** Es una secuencia de pasos idempotentes que se conserva en su
orden original: reordenarlos o repartirlos cambia lo que le pasa a una base de
datos que lleva versiones sin actualizarse, y eso no lo ve ninguna suite —los
tests parten de una base nueva, donde casi todo el bloque no hace nada—. Su
tamaño es el precio de no tocarla; las migraciones nuevas van al registro de
`steps/`, no aquí.
"""


from __future__ import annotations

import json
import os
from typing import Any

from app.storage.migrations.legacy._groups import (
    _migrate_group_active_flag_sqlite,
)

# ── Schema DDL ─────────────────────────────────────────────────────────────────
from app.storage.migrations.legacy._helpers import (
    _RESOURCE_TABLES,
    _SCHEMA_INDEX_DEPS,
    _add_sqlite_column,
)
from app.storage.migrations.legacy._resources import (
    _migrate_legacy_agent_language_labels,
    _migrate_named_resources_sqlite,
)
from app.utils import flog
from app.utils.generators import generate_id


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
            except Exception as exc:  # noqa: BLE001
                # Migración idempotente: se reintenta en el siguiente arranque.
                flog.warning(f"[db] pre-migración {table}.{col} fallida: {exc}")

async def _migrate_sqlite(conn: Any) -> None:
    """Incremental migrations for pre-existing SQLite databases."""
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await cur.fetchall()}
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limit_windows (
            limiter_key TEXT PRIMARY KEY,
            window_start REAL NOT NULL,
            request_count INTEGER NOT NULL
        )
    """)
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
            except Exception as exc:  # noqa: BLE001
                # Migración idempotente: se reintenta en el siguiente arranque.
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
            except Exception as exc:  # noqa: BLE001
                # Migración idempotente: se reintenta en el siguiente arranque.
                flog.warning(f"[db] No se pudo añadir columna {col}: {exc}")

    # Las tablas sociales base pertenecen a app/sql/schema. Los ALTER que
    # siguen son reparaciones para instalaciones antiguas con columnas ausentes.
    await _add_sqlite_column(
        conn, "resource_social", "tags", "TEXT NOT NULL DEFAULT '[]'"
    )
    await _add_sqlite_column(
        conn, "resource_social", "labels", "TEXT NOT NULL DEFAULT '[\"private\"]'"
    )
    await _add_sqlite_column(
        conn, "resource_social", "verified", "INTEGER NOT NULL DEFAULT 0"
    )
    await _add_sqlite_column(
        conn, "agent_workflows", "scope", "TEXT NOT NULL DEFAULT 'private'"
    )
    await _add_sqlite_column(
        conn, "agent_workflows", "labels", "TEXT NOT NULL DEFAULT '[\"private\"]'"
    )
    await _add_sqlite_column(
        conn,
        "skills",
        "category",
        "TEXT CHECK (category IS NULL OR category IN "
        "('ai','messaging','notes','productivity','dev','security','media','data','company'))",
    )

    # 13. Limpiar tokens guardados en plano (pre-hash). Los hasheados son siempre 64 chars hex.
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
        await _add_sqlite_column(conn, table, "is_active", "INTEGER NOT NULL DEFAULT 1")
        await _add_sqlite_column(conn, table, "deactivated_at", "TEXT")

    # Arreglos menores de fechas
    await _add_sqlite_column(conn, "groups", "updated_at", "TEXT")
    await _add_sqlite_column(conn, "memory_files", "created_at", "TEXT")

    # 20. Tokens por mensaje — para mostrar consumo por respuesta al recargar
    # una conversación, no solo en el evento SSE de la sesión activa.
    await _add_sqlite_column(
        conn, "messages", "tokens_in", "INTEGER NOT NULL DEFAULT 0"
    )
    await _add_sqlite_column(
        conn, "messages", "tokens_out", "INTEGER NOT NULL DEFAULT 0"
    )
    # 21. Labels de contenido (incluido idioma) en elementos de knowledge.
    await _add_sqlite_column(
        conn,
        "knowledge_items",
        "labels",
        "TEXT NOT NULL DEFAULT '[\"private\"]'",
    )
    await _migrate_legacy_agent_language_labels(conn, postgres=False)

async def _migrate_users_json_sqlite(conn: Any) -> None:
    """Import users.json into the users table if it exists and the table is empty."""
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
    except Exception as exc:  # noqa: BLE001
        # users.json no se renombra a .migrated si falla: se reintenta.
        flog.warning(f"[db] Importación users.json (SQLite) fallida: {exc}")
