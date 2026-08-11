"""Secuencia versionada de migraciones PostgreSQL."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.legacy import _migrate_pg, _migrate_users_json_pg
from app.storage.migrations.origin_labels import (
    normalize_labels,
    normalize_resource_data,
)
from app.storage.migrations.registry import Migration, run_migrations


async def _table_exists(conn: Any, table: str) -> bool:
    """Ver el homónimo de sqlite.py: las tablas del catálogo oficial antiguo
    ya no forman parte del esquema, así que en una base nueva no existen."""
    return await conn.fetchval("SELECT to_regclass($1)", table) is not None


async def _official_component_metadata(conn: Any) -> None:
    if not await _table_exists(conn, "official_package_components"):
        return
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
    rows = await conn.fetch(
        "SELECT resource_type, resource_id, owner, labels FROM resource_social"
    )
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
    if await _table_exists(conn, "official_package_components"):
        rows = await conn.fetch(
            "SELECT package_id, version, component_id, labels FROM official_package_components"
        )
        await conn.executemany(
            "UPDATE official_package_components SET labels=$1 "
            "WHERE package_id=$2 AND version=$3 AND component_id=$4",
            [
                (
                    normalize_labels(
                        row["labels"], origin="official", drop_production=True
                    ),
                    row["package_id"],
                    row["version"],
                    row["component_id"],
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
            "INSERT INTO resource_labels (resource_type, resource_id, owner_id, label) "
            f"SELECT '{resource_type}', id, owner_id, 'community' FROM {table} "
            "ON CONFLICT (resource_type, resource_id, label) DO NOTHING"
        )


async def _official_copy_mode(conn: Any) -> None:
    if not await _table_exists(conn, "official_package_copies"):
        return
    await conn.execute(
        "ALTER TABLE official_package_copies "
        "ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'copy'"
    )


async def _official_published_components(conn: Any) -> None:
    if not await _table_exists(conn, "official_package_versions"):
        return
    await conn.execute(
        "ALTER TABLE official_package_versions "
        "ADD COLUMN IF NOT EXISTS published_components TEXT NOT NULL DEFAULT '[]'"
    )


async def _official_content_as_resources(conn: Any) -> None:
    """Ver el homónimo de sqlite.py: el contenido oficial pasa a ser recurso
    normal marcado con su fuente, y las tablas del catálogo antiguo sobran."""
    for table in (
        "agents",
        "skills",
        "prompts",
        "tools",
        "knowledge_items",
        "agent_workflows",
    ):
        for column in ("official_source_id", "official_component_id"):
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} TEXT"
            )
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
    if await _table_exists(conn, "official_packages"):
        await conn.execute("""
            INSERT INTO official_sources
                (id, name, description, repository_url, repository_owner,
                 repository_name, tracking_mode, tracking_ref, license,
                 latest_checked_at, last_sync_error, created_at, updated_at)
            SELECT id, name, description, repository_url, repository_owner,
                   repository_name, tracking_mode, tracking_ref, license,
                   latest_checked_at, last_sync_error, created_at, updated_at
            FROM official_packages
            ON CONFLICT (id) DO NOTHING
        """)
    for table in (
        "official_package_copies",
        "official_package_components",
        "official_package_versions",
        "official_packages",
    ):
        await conn.execute(f"DROP TABLE IF EXISTS {table}")


async def _official_source_provenance(conn: Any) -> None:
    for column, definition in {
        "provider": "TEXT NOT NULL DEFAULT 'github'",
        "repository_path": "TEXT NOT NULL DEFAULT ''",
        "owner_id": "TEXT",
        "default_branch": "TEXT NOT NULL DEFAULT 'main'",
        "last_commit_sha": "TEXT",
        "sync_state": "TEXT NOT NULL DEFAULT 'idle'",
    }.items():
        await conn.execute(
            f"ALTER TABLE official_sources ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_source_links (
            source_id TEXT NOT NULL REFERENCES official_sources(id) ON DELETE CASCADE,
            component_key TEXT NOT NULL, resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL, resource_owner_id TEXT NOT NULL,
            source_path TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '',
            explicitly_selected BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY (source_id, component_key),
            UNIQUE (resource_type, resource_id, resource_owner_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_source_resource ON "
        "resource_source_links(resource_type, resource_id, resource_owner_id)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS official_import_drafts (
            id TEXT PRIMARY KEY, source_id TEXT REFERENCES official_sources(id) ON DELETE CASCADE,
            owner_id TEXT NOT NULL, repository_url TEXT NOT NULL, provider TEXT NOT NULL,
            repository_path TEXT NOT NULL, tracking_mode TEXT NOT NULL,
            tracking_ref TEXT NOT NULL, resolved_version TEXT NOT NULL,
            commit_sha TEXT NOT NULL, source_payload TEXT NOT NULL,
            errors TEXT NOT NULL DEFAULT '[]', security_warnings TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending', expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_official_drafts_source ON "
        "official_import_drafts(source_id, status, updated_at DESC)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS official_import_components (
            draft_id TEXT NOT NULL REFERENCES official_import_drafts(id) ON DELETE CASCADE,
            component_key TEXT NOT NULL, payload TEXT NOT NULL,
            selected BOOLEAN NOT NULL DEFAULT FALSE,
            explicitly_selected BOOLEAN NOT NULL DEFAULT FALSE,
            forced_type TEXT, forced_language TEXT,
            security_accepted BOOLEAN NOT NULL DEFAULT FALSE,
            state TEXT NOT NULL DEFAULT 'new', PRIMARY KEY (draft_id, component_key)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_official_components_filter ON "
        "official_import_components(draft_id, state, selected)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS official_source_mappings (
            source_id TEXT NOT NULL REFERENCES official_sources(id) ON DELETE CASCADE,
            source_path TEXT NOT NULL, forced_type TEXT, forced_language TEXT,
            ignored BOOLEAN NOT NULL DEFAULT FALSE,
            dependencies TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
            PRIMARY KEY (source_id, source_path)
        )
    """)
    await conn.execute("""
        UPDATE official_sources SET
            provider=CASE WHEN repository_url LIKE 'https://gitlab.com/%' THEN 'gitlab'
                WHEN repository_url LIKE 'internal://%' THEN 'internal' ELSE 'github' END,
            repository_path=CASE WHEN repository_owner<>''
                THEN repository_owner || '/' || repository_name ELSE repository_name END
    """)
    for resource_type, table in (
        ("agent", "agents"),
        ("skill", "skills"),
        ("prompt", "prompts"),
        ("tool", "tools"),
        ("knowledge", "knowledge_items"),
        ("workflow", "agent_workflows"),
    ):
        await conn.execute(
            "INSERT INTO resource_source_links "
            "(source_id,component_key,resource_type,resource_id,resource_owner_id,"
            "source_path,content_hash,commit_sha,created_at,updated_at) "
            f"SELECT official_source_id, official_component_id, '{resource_type}', id, owner_id, "
            "'', '', '', NOW()::text, NOW()::text "
            f"FROM {table} WHERE official_source_id IS NOT NULL "
            "AND official_component_id IS NOT NULL ON CONFLICT DO NOTHING"
        )
    await conn.execute("""
        UPDATE official_sources s SET owner_id=(
            SELECT MIN(resource_owner_id) FROM resource_source_links l
            WHERE l.source_id=s.id HAVING COUNT(DISTINCT resource_owner_id)=1
        ) WHERE owner_id IS NULL
    """)


async def _official_explicit_selection(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE resource_source_links ADD COLUMN IF NOT EXISTS "
        "explicitly_selected BOOLEAN NOT NULL DEFAULT TRUE"
    )


POSTGRES_MIGRATIONS = (
    Migration(1, "legacy_schema_catchup", _migrate_pg, repeatable=True),
    Migration(2, "users_json_to_relational", _migrate_users_json_pg, repeatable=True),
    Migration(3, "official_component_metadata", _official_component_metadata),
    Migration(4, "resource_origin_labels", _resource_origin_labels),
    Migration(5, "official_copy_mode", _official_copy_mode),
    Migration(6, "official_published_components", _official_published_components),
    Migration(7, "official_content_as_resources", _official_content_as_resources),
    Migration(8, "official_source_provenance", _official_source_provenance),
    Migration(9, "official_explicit_selection", _official_explicit_selection),
)


async def run_postgres_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "postgres", POSTGRES_MIGRATIONS)
