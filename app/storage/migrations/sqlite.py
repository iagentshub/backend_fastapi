"""Secuencia versionada de migraciones SQLite."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.legacy import (
    _migrate_sqlite,
    _migrate_users_json_sqlite,
)
from app.storage.migrations.registry import Migration, run_migrations

SQLITE_MIGRATIONS = (
    Migration(1, "legacy_schema_catchup", _migrate_sqlite, repeatable=True),
    Migration(
        2, "users_json_to_relational", _migrate_users_json_sqlite, repeatable=True
    ),
)


async def run_sqlite_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "sqlite", SQLITE_MIGRATIONS)
