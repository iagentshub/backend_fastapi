"""Secuencia versionada de migraciones PostgreSQL."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.legacy import _migrate_pg, _migrate_users_json_pg
from app.storage.migrations.registry import Migration, run_migrations

POSTGRES_MIGRATIONS = (
    Migration(1, "legacy_schema_catchup", _migrate_pg, repeatable=True),
    Migration(2, "users_json_to_relational", _migrate_users_json_pg, repeatable=True),
)


async def run_postgres_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "postgres", POSTGRES_MIGRATIONS)
