"""Secuencia versionada de migraciones SQLite."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.legacy import (
    _migrate_sqlite,
    _migrate_users_json_sqlite,
)
from app.storage.migrations.registry import Migration, run_migrations


async def _official_component_metadata(conn: Any) -> None:
    rows = await conn.execute_fetchall("PRAGMA table_info(official_package_components)")
    columns = {str(row[1]) for row in rows}
    if "labels" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_components "
            "ADD COLUMN labels TEXT NOT NULL DEFAULT '[\"production\"]'"
        )
    if "dependencies" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_components "
            "ADD COLUMN dependencies TEXT NOT NULL DEFAULT '[]'"
        )


SQLITE_MIGRATIONS = (
    Migration(1, "legacy_schema_catchup", _migrate_sqlite, repeatable=True),
    Migration(
        2, "users_json_to_relational", _migrate_users_json_sqlite, repeatable=True
    ),
    Migration(3, "official_component_metadata", _official_component_metadata),
)


async def run_sqlite_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "sqlite", SQLITE_MIGRATIONS)
