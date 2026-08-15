"""Secuencia versionada de migraciones SQLite."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from datetime import datetime, timezone
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
            [(normalize_resource_data(row[2]), row[0], row[1]) for row in rows],
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


async def _official_source_provenance(conn: Any) -> None:
    """Normaliza procedencia, borradores y mappings sin perder columnas legacy."""
    columns = {
        str(row[1])
        for row in await conn.execute_fetchall("PRAGMA table_info(official_sources)")
    }
    additions = {
        "provider": "TEXT NOT NULL DEFAULT 'github'",
        "repository_path": "TEXT NOT NULL DEFAULT ''",
        "owner_id": "TEXT",
        "default_branch": "TEXT NOT NULL DEFAULT 'main'",
        "last_commit_sha": "TEXT",
        "sync_state": "TEXT NOT NULL DEFAULT 'idle'",
    }
    for column, definition in additions.items():
        if column not in columns:
            await conn.execute(
                f"ALTER TABLE official_sources ADD COLUMN {column} {definition}"
            )
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS resource_source_links (
            source_id TEXT NOT NULL, component_key TEXT NOT NULL,
            resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
            resource_owner_id TEXT NOT NULL, source_path TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '', commit_sha TEXT NOT NULL DEFAULT '',
            explicitly_selected INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (source_id, component_key),
            UNIQUE (resource_type, resource_id, resource_owner_id),
            FOREIGN KEY (source_id) REFERENCES official_sources(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_resource_source_resource
            ON resource_source_links(resource_type, resource_id, resource_owner_id);
        CREATE TABLE IF NOT EXISTS official_import_drafts (
            id TEXT PRIMARY KEY, source_id TEXT, owner_id TEXT NOT NULL,
            repository_url TEXT NOT NULL, provider TEXT NOT NULL,
            repository_path TEXT NOT NULL, tracking_mode TEXT NOT NULL,
            tracking_ref TEXT NOT NULL, resolved_version TEXT NOT NULL,
            commit_sha TEXT NOT NULL, source_payload TEXT NOT NULL,
            errors TEXT NOT NULL DEFAULT '[]',
            security_warnings TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending', expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY (source_id) REFERENCES official_sources(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_official_drafts_source
            ON official_import_drafts(source_id, status, updated_at DESC);
        CREATE TABLE IF NOT EXISTS official_import_components (
            draft_id TEXT NOT NULL, component_key TEXT NOT NULL, payload TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0,
            explicitly_selected INTEGER NOT NULL DEFAULT 0,
            forced_type TEXT, forced_language TEXT,
            security_accepted INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'new',
            PRIMARY KEY (draft_id, component_key),
            FOREIGN KEY (draft_id) REFERENCES official_import_drafts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_official_components_filter
            ON official_import_components(draft_id, state, selected);
        CREATE TABLE IF NOT EXISTS official_source_mappings (
            source_id TEXT NOT NULL, source_path TEXT NOT NULL,
            forced_type TEXT, forced_language TEXT,
            ignored INTEGER NOT NULL DEFAULT 0,
            dependencies TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
            PRIMARY KEY (source_id, source_path),
            FOREIGN KEY (source_id) REFERENCES official_sources(id) ON DELETE CASCADE
        );
    """)
    await conn.execute("""
        UPDATE official_sources
        SET provider=CASE
            WHEN repository_url LIKE 'https://gitlab.com/%' THEN 'gitlab'
            WHEN repository_url LIKE 'internal://%' THEN 'internal'
            ELSE 'github' END,
            repository_path=CASE
            WHEN repository_owner<>'' THEN repository_owner || '/' || repository_name
            ELSE repository_name END
    """)
    now = datetime.now(timezone.utc).isoformat()
    for resource_type, table in (
        ("agent", "agents"),
        ("skill", "skills"),
        ("prompt", "prompts"),
        ("tool", "tools"),
        ("knowledge", "knowledge_items"),
        ("workflow", "agent_workflows"),
    ):
        await conn.execute(
            "INSERT OR IGNORE INTO resource_source_links "
            "(source_id,component_key,resource_type,resource_id,resource_owner_id,"
            "source_path,content_hash,commit_sha,created_at,updated_at) "
            f"SELECT official_source_id, official_component_id, ?, id, owner_id, '', '', '', ?, ? FROM {table} "
            "WHERE official_source_id IS NOT NULL AND official_component_id IS NOT NULL",
            (resource_type, now, now),
        )
    await conn.execute("""
        UPDATE official_sources
        SET owner_id=(
            SELECT MIN(resource_owner_id) FROM resource_source_links l
            WHERE l.source_id=official_sources.id
            HAVING COUNT(DISTINCT resource_owner_id)=1
        )
        WHERE owner_id IS NULL
    """)


async def _official_explicit_selection(conn: Any) -> None:
    columns = {
        str(row[1])
        for row in await conn.execute_fetchall(
            "PRAGMA table_info(resource_source_links)"
        )
    }
    if "explicitly_selected" not in columns:
        await conn.execute(
            "ALTER TABLE resource_source_links ADD COLUMN "
            "explicitly_selected INTEGER NOT NULL DEFAULT 1"
        )


async def _official_source_import_modes(conn: Any) -> None:
    columns = {
        str(row[1])
        for row in await conn.execute_fetchall("PRAGMA table_info(official_sources)")
    }
    if "import_mode" not in columns:
        await conn.execute(
            "ALTER TABLE official_sources ADD COLUMN "
            "import_mode TEXT NOT NULL DEFAULT 'deterministic'"
        )
    if "llm_connection_id" not in columns:
        await conn.execute(
            "ALTER TABLE official_sources ADD COLUMN llm_connection_id TEXT"
        )


async def _official_tool_languages(conn: Any) -> None:
    for table in ("official_import_components", "official_source_mappings"):
        columns = {
            str(row[1])
            for row in await conn.execute_fetchall(f"PRAGMA table_info({table})")
        }
        if "forced_tool_language" not in columns:
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN forced_tool_language TEXT"
            )


async def _connection_provider_accounts(conn: Any) -> None:
    columns = {
        str(row[1])
        for row in await conn.execute_fetchall("PRAGMA table_info(connections)")
    }
    if "provider_account_id" not in columns:
        await conn.execute(
            "ALTER TABLE connections ADD COLUMN provider_account_id TEXT"
        )
    rows = await conn.execute_fetchall(
        "SELECT id,owner_id,data FROM connections WHERE provider_account_id IS NULL"
    )
    for row in rows:
        try:
            payload = json.loads(row[2] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        account_id = str(payload.get("_account_id") or "").strip()
        if not account_id:
            continue
        accounts = await conn.execute_fetchall(
            "SELECT 1 FROM accounts WHERE id=? AND owner_id=?", (account_id, row[1])
        )
        if accounts:
            await conn.execute(
                "UPDATE connections SET provider_account_id=? WHERE id=?",
                (account_id, row[0]),
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
    """Repara agentes de usuario guardados como públicos pero no publicados.

    Versiones anteriores permitían ``agents.scope='public'`` sin crear la fila
    de ``resource_social``. Solo se recuperan agentes con label pública y se
    excluyen los agentes de sistema para no alterar su exposición histórica.
    """
    await conn.execute("""
        INSERT INTO resource_social (
            resource_type, resource_id, owner, name, description, is_public,
            category, trial_missing_deps, tags, labels, updated_at
        )
        SELECT
            'agent', a.id, a.owner_id, a.name,
            COALESCE(json_extract(a.data, '$.description'), ''), 1,
            'Other', 'warn',
            COALESCE(json_extract(a.data, '$.tags'), '[]'),
            COALESCE(json_extract(a.data, '$.labels'), '["public","community"]'),
            a.updated_at
        FROM agents a
        WHERE a.scope='public'
          AND a.owner_id!='__public__'
          AND json_valid(a.data)
          AND EXISTS (
              SELECT 1 FROM json_each(json_extract(a.data, '$.labels'))
              WHERE value='public'
          )
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
            scope TEXT NOT NULL DEFAULT 'private', is_active INTEGER NOT NULL DEFAULT 1,
            deactivated_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_packs_owner "
        "ON knowledge_packs(owner_id, created_at DESC)"
    )


async def _knowledge_file_metadata(conn: Any) -> None:
    """Guarda el MIME y peso original de documentos e imágenes individuales."""
    cursor = await conn.execute("PRAGMA table_info(knowledge_items)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "mime_type" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_items ADD COLUMN mime_type TEXT NOT NULL DEFAULT ''"
        )
    if "size_bytes" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_items ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0"
        )
    await conn.execute(
        "UPDATE knowledge_items SET is_active=1,deactivated_at=NULL WHERE is_active=0"
    )
    await conn.execute(
        "UPDATE knowledge_packs SET is_active=1,deactivated_at=NULL WHERE is_active=0"
    )


async def _knowledge_pack_sources(conn: Any) -> None:
    """Registra si un pack es copia, referencia o instantánea sincronizable."""
    cursor = await conn.execute("PRAGMA table_info(knowledge_packs)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "source_mode" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_packs ADD COLUMN "
            "source_mode TEXT NOT NULL DEFAULT 'upload'"
        )
    if "last_synced_at" not in columns:
        await conn.execute("ALTER TABLE knowledge_packs ADD COLUMN last_synced_at TEXT")


async def _knowledge_pack_upload_sessions(conn: Any) -> None:
    cursor = await conn.execute("PRAGMA table_info(knowledge_packs)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "upload_status" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_packs ADD COLUMN "
            "upload_status TEXT NOT NULL DEFAULT 'ready'"
        )


async def _knowledge_item_checksums(conn: Any) -> None:
    """Añade SHA-256 y rellena los objetos existentes de forma idempotente."""
    cursor = await conn.execute("PRAGMA table_info(knowledge_items)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "checksum" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_items ADD COLUMN checksum TEXT NOT NULL DEFAULT ''"
        )
    if await _table_exists(conn, "knowledge_pack_items"):
        rows = await conn.execute_fetchall(
            "SELECT k.id,k.content,COALESCE(pi.checksum,'') AS pack_checksum "
            "FROM knowledge_items k LEFT JOIN knowledge_pack_items pi "
            "ON pi.knowledge_id=k.id WHERE k.checksum=''"
        )
    else:
        rows = await conn.execute_fetchall(
            "SELECT id,content,'' AS pack_checksum FROM knowledge_items "
            "WHERE checksum=''"
        )
    for row in rows:
        checksum = (
            str(row[2] or "") or hashlib.sha256(str(row[1] or "").encode()).hexdigest()
        )
        await conn.execute(
            "UPDATE knowledge_items SET checksum=? WHERE id=?", (checksum, row[0])
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_checksum "
        "ON knowledge_items(checksum) WHERE checksum <> ''"
    )


async def _knowledge_items_pack_membership(conn: Any) -> None:
    """Convierte la relación de packs en una relación uno-a-muchos directa."""
    cursor = await conn.execute("PRAGMA table_info(knowledge_items)")
    columns = {row[1] for row in await cursor.fetchall()}
    for column, definition in (
        ("mime_type", "TEXT NOT NULL DEFAULT ''"),
        ("size_bytes", "INTEGER NOT NULL DEFAULT 0"),
        ("pack_id", "TEXT"),
        ("pack_relative_path", "TEXT NOT NULL DEFAULT ''"),
        ("pack_kind", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in columns:
            await conn.execute(
                f"ALTER TABLE knowledge_items ADD COLUMN {column} {definition}"
            )
    if await _table_exists(conn, "knowledge_pack_items"):
        await conn.execute(
            "UPDATE knowledge_items SET "
            "pack_id=(SELECT i.pack_id FROM knowledge_pack_items i "
            "WHERE i.knowledge_id=knowledge_items.id LIMIT 1),"
            "pack_relative_path=COALESCE((SELECT i.relative_path "
            "FROM knowledge_pack_items i WHERE i.knowledge_id=knowledge_items.id "
            "LIMIT 1),''),"
            "pack_kind=COALESCE((SELECT i.kind FROM knowledge_pack_items i "
            "WHERE i.knowledge_id=knowledge_items.id LIMIT 1),'') "
            "WHERE EXISTS (SELECT 1 FROM knowledge_pack_items i "
            "WHERE i.knowledge_id=knowledge_items.id)"
        )
        item_cursor = await conn.execute("PRAGMA table_info(knowledge_pack_items)")
        item_columns = {row[1] for row in await item_cursor.fetchall()}
        if "mime_type" in item_columns:
            await conn.execute(
                "UPDATE knowledge_items SET mime_type=COALESCE(NULLIF(mime_type,''),"
                "(SELECT i.mime_type FROM knowledge_pack_items i "
                "WHERE i.knowledge_id=knowledge_items.id LIMIT 1),'')"
            )
        if "size_bytes" in item_columns:
            await conn.execute(
                "UPDATE knowledge_items SET size_bytes=CASE WHEN size_bytes=0 THEN "
                "COALESCE((SELECT i.size_bytes FROM knowledge_pack_items i "
                "WHERE i.knowledge_id=knowledge_items.id LIMIT 1),0) "
                "ELSE size_bytes END"
            )
        await conn.execute("DROP TABLE knowledge_pack_items")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_pack "
        "ON knowledge_items(pack_id,pack_relative_path) WHERE pack_id IS NOT NULL"
    )


async def _knowledge_item_metadata_repair(conn: Any) -> None:
    """Recupera metadatos catalogados que sobrevivieron en el contenido."""
    rows = await conn.execute_fetchall(
        "SELECT id,source,content,mime_type,size_bytes FROM knowledge_items "
        "WHERE mime_type='' OR size_bytes=0"
    )
    for row in rows:
        content = str(row[2] or "")
        mime_match = re.search(r"(?:Tipo|Type):\s*([^\s]+)", content)
        size_match = re.search(r"(?:Tamano|Tamaño|Size):\s*(\d+)\s*bytes", content)
        mime_type = str(row[3] or "")
        if not mime_type:
            mime_type = (
                str(mime_match.group(1))
                if mime_match
                else mimetypes.guess_type(str(row[1] or ""))[0] or ""
            )
        size_bytes = int(row[4] or 0)
        if size_bytes == 0 and size_match:
            size_bytes = int(size_match.group(1))
        await conn.execute(
            "UPDATE knowledge_items SET mime_type=?,size_bytes=? WHERE id=?",
            (mime_type, size_bytes, row[0]),
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_mime_type "
        "ON knowledge_items(mime_type) WHERE mime_type <> ''"
    )


async def _remove_obsolete_knowledge_pack_items(conn: Any) -> None:
    """Consolida cualquier relación legacy y elimina su tabla auxiliar."""
    await _knowledge_items_pack_membership(conn)


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
)


async def run_sqlite_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "sqlite", SQLITE_MIGRATIONS)
