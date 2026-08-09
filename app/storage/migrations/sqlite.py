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


async def _table_exists(conn: Any, table: str) -> bool:
    """Las tablas del catálogo oficial antiguo ya no están en el esquema.

    Las migraciones que las tocaban solo tienen sentido sobre bases de datos
    que las traían: en una nueva no existen y el ALTER/SELECT fallaría.
    """
    rows = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return bool(rows)


async def _official_component_metadata(conn: Any) -> None:
    if not await _table_exists(conn, "official_package_components"):
        return
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
    if await _table_exists(conn, "official_package_components"):
        rows = await conn.execute_fetchall(
            "SELECT rowid, labels FROM official_package_components"
        )
        await conn.executemany(
            "UPDATE official_package_components SET labels=? WHERE rowid=?",
            [
                (
                    normalize_labels(row[1], origin="official", drop_production=True),
                    row[0],
                )
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
    if not await _table_exists(conn, "official_package_copies"):
        return
    rows = await conn.execute_fetchall("PRAGMA table_info(official_package_copies)")
    columns = {str(row[1]) for row in rows}
    if "mode" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_copies "
            "ADD COLUMN mode TEXT NOT NULL DEFAULT 'copy'"
        )


async def _official_published_components(conn: Any) -> None:
    if not await _table_exists(conn, "official_package_versions"):
        return
    rows = await conn.execute_fetchall("PRAGMA table_info(official_package_versions)")
    columns = {str(row[1]) for row in rows}
    if "published_components" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_versions "
            "ADD COLUMN published_components TEXT NOT NULL DEFAULT '[]'"
        )


_OFFICIAL_RESOURCE_TABLES = (
    "agents",
    "skills",
    "prompts",
    "tools",
    "knowledge_items",
    "agent_workflows",
)


async def _official_content_as_resources(conn: Any) -> None:
    """El contenido oficial pasa a ser recurso normal marcado con su fuente.

    Antes vivía en tablas propias (versiones, componentes, copias) y solo se
    convertía en recurso al enlazarlo o copiarlo. Ahora un objeto oficial es
    una fila igual que las demás con ``official_source_id``, así que esas
    tablas se quedan sin uso y se eliminan; las fuentes se conservan para que
    el admin solo tenga que volver a sincronizar.
    """
    for table in _OFFICIAL_RESOURCE_TABLES:
        rows = await conn.execute_fetchall(f"PRAGMA table_info({table})")
        columns = {str(row[1]) for row in rows}
        for column in ("official_source_id", "official_component_id"):
            if column not in columns:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_official "
            f"ON {table}(official_source_id)"
        )

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS official_sources (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            description         TEXT NOT NULL DEFAULT '',
            repository_url      TEXT NOT NULL UNIQUE,
            repository_owner    TEXT NOT NULL DEFAULT '',
            repository_name     TEXT NOT NULL DEFAULT '',
            tracking_mode       TEXT NOT NULL DEFAULT 'release',
            tracking_ref        TEXT NOT NULL DEFAULT 'main',
            license             TEXT NOT NULL DEFAULT '',
            last_version        TEXT,
            latest_checked_at   TEXT,
            last_sync_error     TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )
    """)
    tables = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='official_packages'"
    )
    if tables:
        await conn.execute("""
            INSERT OR IGNORE INTO official_sources
                (id, name, description, repository_url, repository_owner,
                 repository_name, tracking_mode, tracking_ref, license,
                 latest_checked_at, last_sync_error, created_at, updated_at)
            SELECT id, name, description, repository_url, repository_owner,
                   repository_name, tracking_mode, tracking_ref, license,
                   latest_checked_at, last_sync_error, created_at, updated_at
            FROM official_packages
        """)
    for table in (
        "official_package_copies",
        "official_package_components",
        "official_package_versions",
        "official_packages",
    ):
        await conn.execute(f"DROP TABLE IF EXISTS {table}")


SQLITE_MIGRATIONS = (
    Migration(1, "legacy_schema_catchup", _migrate_sqlite, repeatable=True),
    Migration(
        2, "users_json_to_relational", _migrate_users_json_sqlite, repeatable=True
    ),
    Migration(3, "official_component_metadata", _official_component_metadata),
    Migration(4, "resource_origin_labels", _resource_origin_labels),
    Migration(5, "official_copy_mode", _official_copy_mode),
    Migration(6, "official_published_components", _official_published_components),
    Migration(7, "official_content_as_resources", _official_content_as_resources),
)


async def run_sqlite_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "sqlite", SQLITE_MIGRATIONS)
