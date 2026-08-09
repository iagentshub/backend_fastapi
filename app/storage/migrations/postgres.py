"""Secuencia versionada de migraciones PostgreSQL."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.legacy import _migrate_pg, _migrate_users_json_pg
from app.storage.migrations.registry import Migration, run_migrations


async def _official_component_metadata(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE official_package_components "
        "ADD COLUMN IF NOT EXISTS labels TEXT NOT NULL DEFAULT '[\"production\"]'"
    )
    await conn.execute(
        "ALTER TABLE official_package_components "
        "ADD COLUMN IF NOT EXISTS dependencies TEXT NOT NULL DEFAULT '[]'"
    )


POSTGRES_MIGRATIONS = (
    Migration(1, "legacy_schema_catchup", _migrate_pg, repeatable=True),
    Migration(2, "users_json_to_relational", _migrate_users_json_pg, repeatable=True),
    Migration(3, "official_component_metadata", _official_component_metadata),
)


async def run_postgres_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "postgres", POSTGRES_MIGRATIONS)
