"""Secuencia versionada de migraciones PostgreSQL."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.legacy import _migrate_pg, _migrate_users_json_pg
from app.storage.migrations.origin_labels import (
    normalize_labels,
    normalize_resource_data,
)
from app.storage.migrations.registry import Migration, run_migrations


async def _official_component_metadata(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE official_package_components "
        "ADD COLUMN IF NOT EXISTS labels TEXT NOT NULL DEFAULT '[\"official\"]'"
    )
    await conn.execute(
        "ALTER TABLE official_package_components "
        "ADD COLUMN IF NOT EXISTS dependencies TEXT NOT NULL DEFAULT '[]'"
    )


async def _resource_origin_labels(conn: Any) -> None:
    for table in ("agents", "skills", "prompts", "tools"):
        rows = await conn.fetch(f"SELECT id, owner_id, data FROM {table}")
        await conn.executemany(
            f"UPDATE {table} SET data=$1 WHERE id=$2 AND owner_id=$3",
            [
                (normalize_resource_data(row["data"]), row["id"], row["owner_id"])
                for row in rows
            ],
        )
    for table in ("knowledge_items", "agent_workflows"):
        rows = await conn.fetch(f"SELECT id, labels FROM {table}")
        await conn.executemany(
            f"UPDATE {table} SET labels=$1 WHERE id=$2",
            [
                (normalize_labels(row["labels"], origin="community"), row["id"])
                for row in rows
            ],
        )
    rows = await conn.fetch("SELECT resource_type, resource_id, owner, labels FROM resource_social")
    await conn.executemany(
        "UPDATE resource_social SET labels=$1 WHERE resource_type=$2 AND resource_id=$3 AND owner=$4",
        [
            (
                normalize_labels(row["labels"], origin="community"),
                row["resource_type"],
                row["resource_id"],
                row["owner"],
            )
            for row in rows
        ],
    )
    rows = await conn.fetch(
        "SELECT package_id, version, component_id, labels FROM official_package_components"
    )
    await conn.executemany(
        "UPDATE official_package_components SET labels=$1 "
        "WHERE package_id=$2 AND version=$3 AND component_id=$4",
        [
            (
                normalize_labels(row["labels"], origin="official", drop_production=True),
                row["package_id"],
                row["version"],
                row["component_id"],
            )
            for row in rows
        ],
    )
    await conn.execute("DELETE FROM resource_labels WHERE label IN ('official', 'community')")
    for resource_type, table in (
        ("agent", "agents"),
        ("skill", "skills"),
        ("prompt", "prompts"),
        ("tool", "tools"),
        ("knowledge", "knowledge_items"),
        ("workflow", "agent_workflows"),
    ):
        await conn.execute(
            "INSERT INTO resource_labels (resource_type, resource_id, owner_id, label) "
            f"SELECT '{resource_type}', id, owner_id, 'community' FROM {table} "
            "ON CONFLICT (resource_type, resource_id, label) DO NOTHING"
        )


async def _official_copy_mode(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE official_package_copies "
        "ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'copy'"
    )


POSTGRES_MIGRATIONS = (
    Migration(1, "legacy_schema_catchup", _migrate_pg, repeatable=True),
    Migration(2, "users_json_to_relational", _migrate_users_json_pg, repeatable=True),
    Migration(3, "official_component_metadata", _official_component_metadata),
    Migration(4, "resource_origin_labels", _resource_origin_labels),
    Migration(5, "official_copy_mode", _official_copy_mode),
)


async def run_postgres_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "postgres", POSTGRES_MIGRATIONS)
