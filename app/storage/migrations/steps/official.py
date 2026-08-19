"""Pasos del dominio de fuentes oficiales, en sus dos dialectos.

Las dos variantes de cada paso van juntas y seguidas a propósito: separarlas por
motor es cómo se corrige una y se olvida la otra —el bug que originó
`tests/storage/test_migraciones_pg_traducidas.py`—.
"""



from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.storage.migrations.steps.misc import _table_exists_pg, _table_exists_sqlite

_OFFICIAL_RESOURCE_TABLES = (
    "agents",
    "skills",
    "prompts",
    "tools",
    "knowledge_items",
    "agent_workflows",
)


async def _official_component_metadata_sqlite(conn: Any) -> None:
    if not await _table_exists_sqlite(conn, "official_package_components"):
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

async def _official_component_metadata_pg(conn: Any) -> None:
    if not await _table_exists_pg(conn, "official_package_components"):
        return
    await conn.execute(
        "ALTER TABLE official_package_components "
        "ADD COLUMN IF NOT EXISTS labels TEXT NOT NULL DEFAULT '[\"official\"]'"
    )
    await conn.execute(
        "ALTER TABLE official_package_components "
        "ADD COLUMN IF NOT EXISTS dependencies TEXT NOT NULL DEFAULT '[]'"
    )

async def _official_copy_mode_sqlite(conn: Any) -> None:
    if not await _table_exists_sqlite(conn, "official_package_copies"):
        return
    rows = await conn.execute_fetchall("PRAGMA table_info(official_package_copies)")
    columns = {str(row[1]) for row in rows}
    if "mode" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_copies "
            "ADD COLUMN mode TEXT NOT NULL DEFAULT 'copy'"
        )

async def _official_copy_mode_pg(conn: Any) -> None:
    if not await _table_exists_pg(conn, "official_package_copies"):
        return
    await conn.execute(
        "ALTER TABLE official_package_copies "
        "ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'copy'"
    )

async def _official_published_components_sqlite(conn: Any) -> None:
    if not await _table_exists_sqlite(conn, "official_package_versions"):
        return
    rows = await conn.execute_fetchall("PRAGMA table_info(official_package_versions)")
    columns = {str(row[1]) for row in rows}
    if "published_components" not in columns:
        await conn.execute(
            "ALTER TABLE official_package_versions "
            "ADD COLUMN published_components TEXT NOT NULL DEFAULT '[]'"
        )

async def _official_published_components_pg(conn: Any) -> None:
    if not await _table_exists_pg(conn, "official_package_versions"):
        return
    await conn.execute(
        "ALTER TABLE official_package_versions "
        "ADD COLUMN IF NOT EXISTS published_components TEXT NOT NULL DEFAULT '[]'"
    )

async def _official_content_as_resources_sqlite(conn: Any) -> None:
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

async def _official_content_as_resources_pg(conn: Any) -> None:
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
    if await _table_exists_pg(conn, "official_packages"):
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

async def _official_source_provenance_sqlite(conn: Any) -> None:
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

async def _official_source_provenance_pg(conn: Any) -> None:
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

async def _official_explicit_selection_sqlite(conn: Any) -> None:
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

async def _official_explicit_selection_pg(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE resource_source_links ADD COLUMN IF NOT EXISTS "
        "explicitly_selected BOOLEAN NOT NULL DEFAULT TRUE"
    )

async def _official_source_import_modes_sqlite(conn: Any) -> None:
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

async def _official_source_import_modes_pg(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE official_sources ADD COLUMN IF NOT EXISTS "
        "import_mode TEXT NOT NULL DEFAULT 'deterministic'"
    )
    await conn.execute(
        "ALTER TABLE official_sources ADD COLUMN IF NOT EXISTS llm_connection_id TEXT"
    )

async def _official_tool_languages_sqlite(conn: Any) -> None:
    for table in ("official_import_components", "official_source_mappings"):
        columns = {
            str(row[1])
            for row in await conn.execute_fetchall(f"PRAGMA table_info({table})")
        }
        if "forced_tool_language" not in columns:
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN forced_tool_language TEXT"
            )

async def _official_tool_languages_pg(conn: Any) -> None:
    for table in ("official_import_components", "official_source_mappings"):
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS forced_tool_language TEXT"
        )
