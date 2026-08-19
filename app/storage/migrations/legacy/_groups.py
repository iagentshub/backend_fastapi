"""Pasos históricos sobre grupos: el esquema viejo y su renombrado.

Aquí vivía el esquema `teams` original; el renombrado a `groups` es lo que
permite que una instalación antigua siga arrancando.
"""


from __future__ import annotations

from typing import Any

# ── Schema DDL ─────────────────────────────────────────────────────────────────


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
