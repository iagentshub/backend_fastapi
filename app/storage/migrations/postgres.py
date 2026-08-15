"""Secuencia versionada de migraciones PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
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


async def _official_source_import_modes(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE official_sources ADD COLUMN IF NOT EXISTS "
        "import_mode TEXT NOT NULL DEFAULT 'deterministic'"
    )
    await conn.execute(
        "ALTER TABLE official_sources ADD COLUMN IF NOT EXISTS llm_connection_id TEXT"
    )


async def _official_tool_languages(conn: Any) -> None:
    for table in ("official_import_components", "official_source_mappings"):
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS forced_tool_language TEXT"
        )


async def _connection_provider_accounts(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE connections ADD COLUMN IF NOT EXISTS provider_account_id TEXT"
    )
    rows = await conn.fetchall(
        "SELECT id,owner_id,data FROM connections WHERE provider_account_id IS NULL"
    )
    for row in rows:
        try:
            payload = json.loads(row["data"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        account_id = str(payload.get("_account_id") or "").strip()
        if not account_id:
            continue
        account = await conn.fetchone(
            "SELECT 1 FROM accounts WHERE id=? AND owner_id=?",
            (account_id, row["owner_id"]),
        )
        if account:
            await conn.execute(
                "UPDATE connections SET provider_account_id=? WHERE id=?",
                (account_id, row["id"]),
            )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_connections_provider_account "
        "ON connections(owner_id,provider_account_id)"
    )


async def _resource_social_origin_index(conn: Any) -> None:
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rsoc_link_origin ON resource_social("
        "owner,linked_to_user,linked_to_id,resource_type) "
        "WHERE linked_to_id IS NOT NULL"
    )


async def _public_agents_in_social_catalog(conn: Any) -> None:
    """Repara agentes de usuario públicos que quedaron fuera de Explore."""
    await conn.execute("""
        INSERT INTO resource_social (
            resource_type, resource_id, owner, name, description, is_public,
            category, trial_missing_deps, tags, labels, updated_at
        )
        SELECT
            'agent', a.id, a.owner_id, a.name,
            COALESCE(a.data::jsonb ->> 'description', ''), 1,
            'Other', 'warn',
            COALESCE((a.data::jsonb -> 'tags')::text, '[]'),
            COALESCE((a.data::jsonb -> 'labels')::text, '["public","community"]'),
            a.updated_at::timestamptz
        FROM agents a
        WHERE a.scope='public'
          AND a.owner_id!='__public__'
          AND (a.data::jsonb -> 'labels') ? 'public'
          AND NOT EXISTS (
              SELECT 1 FROM resource_social rs
              WHERE rs.resource_type='agent' AND rs.resource_id=a.id
                AND rs.owner=a.owner_id
          )
    """)


async def _knowledge_packs(conn: Any) -> None:
    """Añade packs a instalaciones creadas antes del DDL base."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_packs (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', labels TEXT NOT NULL DEFAULT '["private"]',
            scope TEXT NOT NULL DEFAULT 'private', is_active SMALLINT NOT NULL DEFAULT 1,
            deactivated_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_packs_owner "
        "ON knowledge_packs(owner_id, created_at DESC)"
    )


async def _knowledge_file_metadata(conn: Any) -> None:
    """Guarda el MIME y peso original de documentos e imágenes individuales."""
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "mime_type TEXT NOT NULL DEFAULT ''"
    )
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "size_bytes BIGINT NOT NULL DEFAULT 0"
    )
    await conn.execute(
        "UPDATE knowledge_items SET is_active=1,deactivated_at=NULL WHERE is_active=0"
    )
    await conn.execute(
        "UPDATE knowledge_packs SET is_active=1,deactivated_at=NULL WHERE is_active=0"
    )


async def _knowledge_pack_sources(conn: Any) -> None:
    """Registra si un pack es copia, referencia o instantánea sincronizable."""
    await conn.execute(
        "ALTER TABLE knowledge_packs ADD COLUMN IF NOT EXISTS "
        "source_mode TEXT NOT NULL DEFAULT 'upload'"
    )
    await conn.execute(
        "ALTER TABLE knowledge_packs ADD COLUMN IF NOT EXISTS last_synced_at TEXT"
    )


async def _knowledge_pack_upload_sessions(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE knowledge_packs ADD COLUMN IF NOT EXISTS "
        "upload_status TEXT NOT NULL DEFAULT 'ready'"
    )


async def _knowledge_item_checksums(conn: Any) -> None:
    """Añade SHA-256 y rellena los objetos existentes de forma idempotente."""
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "checksum TEXT NOT NULL DEFAULT ''"
    )
    if await _table_exists(conn, "knowledge_pack_items"):
        rows = await conn.fetch(
            "SELECT k.id,k.content,COALESCE(pi.checksum,'') AS pack_checksum "
            "FROM knowledge_items k LEFT JOIN knowledge_pack_items pi "
            "ON pi.knowledge_id=k.id WHERE k.checksum=''"
        )
    else:
        rows = await conn.fetch(
            "SELECT id,content,'' AS pack_checksum FROM knowledge_items "
            "WHERE checksum=''"
        )
    for row in rows:
        checksum = (
            str(row["pack_checksum"] or "")
            or hashlib.sha256(str(row["content"] or "").encode()).hexdigest()
        )
        await conn.execute(
            "UPDATE knowledge_items SET checksum=$1 WHERE id=$2",
            checksum,
            row["id"],
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_checksum "
        "ON knowledge_items(checksum) WHERE checksum <> ''"
    )


async def _knowledge_items_pack_membership(conn: Any) -> None:
    """Convierte la relación de packs en una relación uno-a-muchos directa."""
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS pack_id TEXT"
    )
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "pack_relative_path TEXT NOT NULL DEFAULT ''"
    )
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "pack_kind TEXT NOT NULL DEFAULT ''"
    )
    if await _table_exists(conn, "knowledge_pack_items"):
        await conn.execute(
            "UPDATE knowledge_items k SET pack_id=i.pack_id,"
            "pack_relative_path=i.relative_path,pack_kind=i.kind,"
            "mime_type=CASE WHEN k.mime_type='' THEN i.mime_type ELSE k.mime_type END,"
            "size_bytes=CASE WHEN k.size_bytes=0 THEN i.size_bytes ELSE k.size_bytes END "
            "FROM knowledge_pack_items i WHERE i.knowledge_id=k.id"
        )
        await conn.execute("DROP TABLE knowledge_pack_items")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_pack "
        "ON knowledge_items(pack_id,pack_relative_path) WHERE pack_id IS NOT NULL"
    )


async def _knowledge_item_metadata_repair(conn: Any) -> None:
    """Recupera metadatos catalogados que sobrevivieron en el contenido."""
    rows = await conn.fetch(
        "SELECT id,source,content,mime_type,size_bytes FROM knowledge_items "
        "WHERE mime_type='' OR size_bytes=0"
    )
    for row in rows:
        content = str(row["content"] or "")
        mime_match = re.search(r"(?:Tipo|Type):\s*([^\s]+)", content)
        size_match = re.search(r"(?:Tamano|Tamaño|Size):\s*(\d+)\s*bytes", content)
        mime_type = str(row["mime_type"] or "")
        if not mime_type:
            mime_type = (
                str(mime_match.group(1))
                if mime_match
                else mimetypes.guess_type(str(row["source"] or ""))[0] or ""
            )
        size_bytes = int(row["size_bytes"] or 0)
        if size_bytes == 0 and size_match:
            size_bytes = int(size_match.group(1))
        await conn.execute(
            "UPDATE knowledge_items SET mime_type=$1,size_bytes=$2 WHERE id=$3",
            mime_type,
            size_bytes,
            row["id"],
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_mime_type "
        "ON knowledge_items(mime_type) WHERE mime_type <> ''"
    )


async def _remove_obsolete_knowledge_pack_items(conn: Any) -> None:
    """Consolida cualquier relación legacy y elimina su tabla auxiliar."""
    await _knowledge_items_pack_membership(conn)


async def _pagination_indexes(conn: Any) -> None:
    """Índices compuestos alineados con filtros y órdenes de páginas."""
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_agents_owner_page "
        "ON agents(owner_id,scope,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_skills_owner_page "
        "ON skills(owner_id,scope,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_prompts_owner_page "
        "ON prompts(owner_id,scope,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tools_owner_page "
        "ON tools(owner_id,scope,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_owner_page "
        "ON knowledge_items(owner_id,type,created_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_connections_owner_page "
        "ON connections(owner_id,is_active,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_conv_user_agent_page "
        "ON conversations(user_id,agent_id,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_msg_conv_page "
        "ON messages(conversation_id,created_at DESC,id DESC)",
    )
    for statement in statements:
        await conn.execute(statement)


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
    Migration(10, "official_source_import_modes", _official_source_import_modes),
    Migration(11, "official_tool_languages", _official_tool_languages),
    Migration(12, "connection_provider_accounts", _connection_provider_accounts),
    Migration(13, "resource_social_origin_index", _resource_social_origin_index),
    Migration(14, "public_agents_in_social_catalog", _public_agents_in_social_catalog),
    Migration(15, "knowledge_packs", _knowledge_packs),
    Migration(16, "knowledge_file_metadata", _knowledge_file_metadata),
    Migration(17, "knowledge_pack_sources", _knowledge_pack_sources),
    Migration(18, "knowledge_pack_upload_sessions", _knowledge_pack_upload_sessions),
    Migration(19, "knowledge_item_checksums", _knowledge_item_checksums),
    Migration(20, "knowledge_items_pack_membership", _knowledge_items_pack_membership),
    Migration(21, "knowledge_item_metadata_repair", _knowledge_item_metadata_repair),
    Migration(
        22,
        "remove_obsolete_knowledge_pack_items",
        _remove_obsolete_knowledge_pack_items,
    ),
    Migration(23, "pagination_indexes", _pagination_indexes),
)


async def run_postgres_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "postgres", POSTGRES_MIGRATIONS)
