"""La puesta al día de una instalación PostgreSQL antigua, en un solo bloque.

Mismo criterio que su gemela de SQLite: **no se trocea**. Ver
`_catchup_sqlite.py` para el porqué.
"""


from __future__ import annotations

import json
import os
from typing import Any

from app.storage.migrations.legacy._groups import (
    _migrate_group_active_flag_pg,
)

# ── Schema DDL ─────────────────────────────────────────────────────────────────
from app.storage.migrations.legacy._helpers import (
    _RESOURCE_TABLES,
    _SCHEMA_INDEX_DEPS,
)
from app.storage.migrations.legacy._resources import (
    _migrate_legacy_agent_language_labels,
    _migrate_named_resources_pg,
)
from app.utils import flog
from app.utils.generators import generate_id


async def _pre_migrate_pg(conn: Any) -> None:
    """El equivalente PostgreSQL de `_pre_migrate_sqlite`, que no existía.

    El esquema se re-ejecuta entero en cada arranque y sus `CREATE INDEX`
    nombran columnas que `CREATE TABLE IF NOT EXISTS` no añade a una tabla que
    ya existe. En SQLite eso lo cubría `_pre_migrate_sqlite`; aquí no lo cubría
    nadie, así que una base anterior a la columna respondía *column ... does
    not exist* al crear el índice y **el backend no arrancaba**. Lo hace la
    misma lista, para que añadir un índice sobre una columna nueva no vuelva a
    dejar fuera a uno de los dos motores.
    """
    for tabla, columna, tipo in _SCHEMA_INDEX_DEPS:
        existe = await conn.fetchval(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=$1",
            tabla,
        )
        if not existe:
            continue
        await conn.execute(
            f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {columna} {tipo}"
        )


async def _migrate_pg(conn: Any) -> None:
    """Incremental migrations for pre-existing PostgreSQL databases."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limit_windows (
            limiter_key TEXT PRIMARY KEY,
            window_start DOUBLE PRECISION NOT NULL,
            request_count INTEGER NOT NULL
        )
    """)
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
    await conn.execute("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_pkey")
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
        # `avatar` salió de aquí al mudarse la foto a `user_avatars` (migración
        # 39). Este paso es `repeatable=True` y corre en cada arranque: dejarlo
        # significaba recrear la columna justo después de que la 39 la
        # eliminara, en bucle y sin que nada fallara.
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
    # Las tablas sociales base pertenecen a app/sql/schema. Los ALTER que
    # siguen son reparaciones para instalaciones antiguas con columnas ausentes.
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
    # 21. Labels de contenido (incluido idioma) en elementos de knowledge.
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "labels TEXT NOT NULL DEFAULT '[\"private\"]'"
    )
    await _migrate_legacy_agent_language_labels(conn, postgres=True)

async def _migrate_users_json_pg(conn: Any) -> None:
    """Import users.json into the users table if it exists and the table is empty."""
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
    except Exception as exc:  # noqa: BLE001
        # users.json no se renombra a .migrated si falla: se reintenta.
        flog.warning(f"[db] Importación users.json (PG) fallida: {exc}")
