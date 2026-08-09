"""Secuencia versionada de migraciones SQLite."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.legacy import (
    _migrate_sqlite,
    _migrate_users_json_sqlite,
)
from app.storage.migrations.origin_labels import (
    normalize_labels,
    normalize_resource_data,
)
from app.storage.migrations.registry import Migration, run_migrations


async def _official_component_metadata(conn: Any) -> None:
    rows = await conn.execute_fetchall("PRAGMA table_info(official_package_components)")
    columns = {str(row[1]) for row in rows}
    if "labels" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_components "
            "ADD COLUMN labels TEXT NOT NULL DEFAULT '[\"official\"]'"
        )
    if "dependencies" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_components "
            "ADD COLUMN dependencies TEXT NOT NULL DEFAULT '[]'"
        )


async def _resource_origin_labels(conn: Any) -> None:
    for table in ("agents", "skills", "prompts", "tools"):
        rows = await conn.execute_fetchall(f"SELECT id, owner_id, data FROM {table}")
        await conn.executemany(
            f"UPDATE {table} SET data=? WHERE id=? AND owner_id=?",
            [
                (normalize_resource_data(row[2]), row[0], row[1])
                for row in rows
            ],
        )
    for table in ("knowledge_items", "agent_workflows", "resource_social"):
        rows = await conn.execute_fetchall(f"SELECT rowid, labels FROM {table}")
        await conn.executemany(
            f"UPDATE {table} SET labels=? WHERE rowid=?",
            [(normalize_labels(row[1], origin="community"), row[0]) for row in rows],
        )
    rows = await conn.execute_fetchall(
        "SELECT rowid, labels FROM official_package_components"
    )
    await conn.executemany(
        "UPDATE official_package_components SET labels=? WHERE rowid=?",
        [
            (normalize_labels(row[1], origin="official", drop_production=True), row[0])
            for row in rows
        ],
    )
    await conn.execute(
        "DELETE FROM resource_labels WHERE label IN ('official', 'community')"
    )
    for resource_type, table in (
        ("agent", "agents"),
        ("skill", "skills"),
        ("prompt", "prompts"),
        ("tool", "tools"),
        ("knowledge", "knowledge_items"),
        ("workflow", "agent_workflows"),
    ):
        await conn.execute(
            "INSERT OR IGNORE INTO resource_labels "
            "(resource_type, resource_id, owner_id, label) "
            f"SELECT '{resource_type}', id, owner_id, 'community' FROM {table}"
        )


async def _official_copy_mode(conn: Any) -> None:
    rows = await conn.execute_fetchall("PRAGMA table_info(official_package_copies)")
    columns = {str(row[1]) for row in rows}
    if "mode" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_copies "
            "ADD COLUMN mode TEXT NOT NULL DEFAULT 'copy'"
        )


async def _official_published_components(conn: Any) -> None:
    rows = await conn.execute_fetchall("PRAGMA table_info(official_package_versions)")
    columns = {str(row[1]) for row in rows}
    if "published_components" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_versions "
            "ADD COLUMN published_components TEXT NOT NULL DEFAULT '[]'"
        )


SQLITE_MIGRATIONS = (
    Migration(1, "legacy_schema_catchup", _migrate_sqlite, repeatable=True),
    Migration(
        2, "users_json_to_relational", _migrate_users_json_sqlite, repeatable=True
    ),
    Migration(3, "official_component_metadata", _official_component_metadata),
    Migration(4, "resource_origin_labels", _resource_origin_labels),
    Migration(5, "official_copy_mode", _official_copy_mode),
    Migration(6, "official_published_components", _official_published_components),
)


async def run_sqlite_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "sqlite", SQLITE_MIGRATIONS)
