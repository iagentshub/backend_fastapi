from __future__ import annotations

import hashlib
import sqlite3

import aiosqlite

from app.storage.migrations.registry import Migration, run_migrations


async def test_registry_runs_in_version_order_and_only_once(tmp_path):
    calls: list[int] = []

    async def first(conn):
        calls.append(1)
        await conn.execute("CREATE TABLE migrated (value TEXT)")

    async def second(conn):
        calls.append(2)
        await conn.execute("INSERT INTO migrated (value) VALUES ('ok')")

    migrations = (
        Migration(2, "insert_data", second),
        Migration(1, "create_table", first),
    )
    async with aiosqlite.connect(tmp_path / "registry.db") as conn:
        conn.row_factory = sqlite3.Row
        assert await run_migrations(conn, "sqlite", migrations) == [1, 2]
        assert await run_migrations(conn, "sqlite", migrations) == []
        rows = await conn.execute_fetchall(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        )

    assert calls == [1, 2]
    assert [tuple(row) for row in rows] == [
        (1, "create_table"),
        (2, "insert_data"),
    ]


async def test_registry_rejects_duplicate_versions(tmp_path):
    async def noop(conn):
        return None

    async with aiosqlite.connect(tmp_path / "duplicates.db") as conn:
        try:
            await run_migrations(
                conn,
                "sqlite",
                (Migration(1, "one", noop), Migration(1, "other", noop)),
            )
        except RuntimeError as exc:
            assert "duplicadas" in str(exc)
        else:
            raise AssertionError("Debió rechazar versiones duplicadas")


async def test_repeatable_migration_runs_again_but_is_recorded_once(tmp_path):
    calls = []

    async def repair(conn):
        calls.append("repair")

    migration = Migration(1, "legacy_repair", repair, repeatable=True)
    async with aiosqlite.connect(tmp_path / "repeatable.db") as conn:
        assert await run_migrations(conn, "sqlite", (migration,)) == [1]
        assert await run_migrations(conn, "sqlite", (migration,)) == []
        count = (await conn.execute_fetchall("SELECT COUNT(*) FROM schema_migrations"))[
            0
        ][0]

    assert calls == ["repair", "repair"]
    assert count == 1


async def test_knowledge_checksum_migration_backfills_objects_and_pack_bytes(tmp_path):
    from app.storage.migrations.steps.knowledge import _knowledge_item_checksums_sqlite

    async with aiosqlite.connect(tmp_path / "knowledge-checksum.db") as conn:
        conn.row_factory = sqlite3.Row
        await conn.executescript("""
            CREATE TABLE knowledge_items (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL
            );
            CREATE TABLE knowledge_pack_items (
                pack_id TEXT NOT NULL,
                knowledge_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                checksum TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO knowledge_items VALUES ('text-1', 'contenido normal');
            INSERT INTO knowledge_items VALUES ('file-1', 'texto extraido');
            INSERT INTO knowledge_pack_items VALUES (
                'pack-1', 'file-1', 'scripts/run.sh', 'script', 'raw-sha256'
            );
        """)

        await _knowledge_item_checksums_sqlite(conn)
        await _knowledge_item_checksums_sqlite(conn)
        rows = await conn.execute_fetchall(
            "SELECT id,checksum FROM knowledge_items ORDER BY id"
        )
        indexes = await conn.execute_fetchall("PRAGMA index_list(knowledge_items)")

    checksums = {str(row["id"]): str(row["checksum"]) for row in rows}
    assert checksums == {
        "file-1": "raw-sha256",
        "text-1": hashlib.sha256(b"contenido normal").hexdigest(),
    }
    assert "idx_knowledge_checksum" in {str(row[1]) for row in indexes}


async def test_knowledge_checksum_migration_does_not_require_legacy_pack_table(
    tmp_path,
):
    from app.storage.migrations.steps.knowledge import _knowledge_item_checksums_sqlite

    async with aiosqlite.connect(tmp_path / "knowledge-checksum-direct.db") as conn:
        conn.row_factory = sqlite3.Row
        await conn.executescript("""
            CREATE TABLE knowledge_items (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL
            );
            INSERT INTO knowledge_items VALUES ('text-1', 'contenido normal');
        """)

        await _knowledge_item_checksums_sqlite(conn)
        row = (
            await conn.execute_fetchall(
                "SELECT checksum FROM knowledge_items WHERE id='text-1'"
            )
        )[0]

    assert str(row[0]) == hashlib.sha256(b"contenido normal").hexdigest()


async def test_pack_migration_does_not_create_obsolete_membership_table(tmp_path):
    from app.storage.migrations.steps.knowledge import _knowledge_packs_sqlite

    async with aiosqlite.connect(tmp_path / "knowledge-pack-direct.db") as conn:
        conn.row_factory = sqlite3.Row
        await _knowledge_packs_sqlite(conn)
        tables = {
            str(row[0])
            for row in await conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert "knowledge_packs" in tables
    assert "knowledge_pack_items" not in tables


async def test_pack_membership_migration_moves_relation_to_knowledge_item(tmp_path):
    from app.storage.migrations.steps.knowledge import (
        _knowledge_items_pack_membership_sqlite,
    )

    async with aiosqlite.connect(tmp_path / "pack-membership.db") as conn:
        conn.row_factory = sqlite3.Row
        await conn.executescript("""
            CREATE TABLE knowledge_items (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL
            );
            CREATE TABLE knowledge_pack_items (
                pack_id TEXT NOT NULL,
                knowledge_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                kind TEXT NOT NULL
            );
            INSERT INTO knowledge_items VALUES ('file-1', 'echo ok');
            INSERT INTO knowledge_pack_items VALUES (
                'pack-1', 'file-1', 'scripts/run.sh', 'script'
            );
        """)

        await _knowledge_items_pack_membership_sqlite(conn)
        await _knowledge_items_pack_membership_sqlite(conn)
        row = (
            await conn.execute_fetchall(
                "SELECT pack_id,pack_relative_path,pack_kind "
                "FROM knowledge_items WHERE id='file-1'"
            )
        )[0]
        tables = {
            str(item[0])
            for item in await conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert tuple(row) == ("pack-1", "scripts/run.sh", "script")
    assert "knowledge_pack_items" not in tables


async def test_knowledge_metadata_repair_recovers_catalogued_values(tmp_path):
    from app.storage.migrations.steps.knowledge import (
        _knowledge_item_metadata_repair_sqlite,
    )

    async with aiosqlite.connect(tmp_path / "knowledge-metadata.db") as conn:
        conn.row_factory = sqlite3.Row
        await conn.executescript("""
            CREATE TABLE knowledge_items (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT '',
                size_bytes INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO knowledge_items VALUES (
                'image-1', 'photos/image.jpeg',
                'Tipo: image/jpeg\nTamano: 1234 bytes', '', 0
            );
            INSERT INTO knowledge_items VALUES (
                'pdf-1', 'docs/manual.pdf', 'texto extraído', '', 0
            );
        """)

        await _knowledge_item_metadata_repair_sqlite(conn)
        await _knowledge_item_metadata_repair_sqlite(conn)
        rows = await conn.execute_fetchall(
            "SELECT id,mime_type,size_bytes FROM knowledge_items ORDER BY id"
        )
        indexes = await conn.execute_fetchall("PRAGMA index_list(knowledge_items)")

    assert [tuple(row) for row in rows] == [
        ("image-1", "image/jpeg", 1234),
        ("pdf-1", "application/pdf", 0),
    ]
    assert "idx_knowledge_mime_type" in {str(row[1]) for row in indexes}


async def test_official_published_components_column_is_added_on_old_dbs(tmp_path):
    """La selección publicada se añade a versiones creadas antes de existir."""
    from app.storage.migrations.steps.official import (
        _official_published_components_sqlite,
    )

    async with aiosqlite.connect(tmp_path / "official.db") as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute(
            "CREATE TABLE official_package_versions ("
            "package_id TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL)"
        )
        await conn.execute(
            "INSERT INTO official_package_versions VALUES ('pkg','v1','published')"
        )

        await _official_published_components_sqlite(conn)
        # Idempotente: la segunda pasada no debe fallar ni duplicar la columna.
        await _official_published_components_sqlite(conn)

        rows = await conn.execute_fetchall(
            "PRAGMA table_info(official_package_versions)"
        )
        columns = [str(row[1]) for row in rows]
        stored = await conn.execute_fetchall(
            "SELECT published_components FROM official_package_versions"
        )

    assert columns.count("published_components") == 1
    assert stored[0][0] == "[]"


async def test_connection_provider_migration_backfills_legacy_account_link(tmp_path):
    """La migración se ejecuta con una Connection cruda de aiosqlite."""
    from app.storage.migrations.steps.misc import _connection_provider_accounts_sqlite

    async with aiosqlite.connect(tmp_path / "provider-account.db") as conn:
        conn.row_factory = sqlite3.Row
        await conn.executescript("""
            CREATE TABLE accounts (
                id TEXT NOT NULL, owner_id TEXT NOT NULL, provider TEXT NOT NULL,
                data TEXT NOT NULL, linked_at TEXT NOT NULL,
                PRIMARY KEY (id, owner_id)
            );
            CREATE TABLE connections (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, data TEXT NOT NULL
            );
            INSERT INTO accounts VALUES (
                'account-1', 'alice', 'openai', '{}', 'old'
            );
            INSERT INTO connections VALUES (
                'connection-1', 'alice', '{"_account_id":"account-1"}'
            );
        """)

        await _connection_provider_accounts_sqlite(conn)
        await _connection_provider_accounts_sqlite(conn)
        row = (
            await conn.execute_fetchall(
                "SELECT provider_account_id FROM connections WHERE id='connection-1'"
            )
        )[0]

    assert row[0] == "account-1"


async def test_resource_social_origin_index_is_partial_and_idempotent(tmp_path):
    from app.storage.migrations.steps.shared import _resource_social_origin_index

    async with aiosqlite.connect(tmp_path / "origin-index.db") as conn:
        await conn.execute(
            "CREATE TABLE resource_social ("
            "owner TEXT NOT NULL, linked_to_user TEXT, linked_to_id TEXT, "
            "resource_type TEXT NOT NULL)"
        )
        await _resource_social_origin_index(conn)
        await _resource_social_origin_index(conn)
        indexes = await conn.execute_fetchall("PRAGMA index_list(resource_social)")
        sql = (
            await conn.execute_fetchall(
                "SELECT sql FROM sqlite_master WHERE name='idx_rsoc_link_origin'"
            )
        )[0][0]

    assert [row[1] for row in indexes].count("idx_rsoc_link_origin") == 1
    assert "WHERE linked_to_id IS NOT NULL" in sql


async def test_public_agent_catalog_migration_repairs_missing_social_row(tmp_path):
    from app.storage.migrations.steps.misc import (
        _public_agents_in_social_catalog_sqlite,
    )

    async with aiosqlite.connect(tmp_path / "public-agent.db") as conn:
        conn.row_factory = sqlite3.Row
        await conn.executescript("""
            CREATE TABLE agents (
                id TEXT, owner_id TEXT, name TEXT, scope TEXT, data TEXT,
                updated_at TEXT
            );
            CREATE TABLE resource_social (
                resource_type TEXT, resource_id TEXT, owner TEXT, name TEXT,
                description TEXT, is_public INTEGER, category TEXT,
                trial_missing_deps TEXT, tags TEXT, labels TEXT,
                updated_at TEXT,
                PRIMARY KEY (resource_type, resource_id, owner)
            );
            INSERT INTO agents VALUES (
                'agent-1', 'owner-1', 'Visible', 'public',
                '{"description":"desc","tags":[],"labels":["public","community"]}',
                '2026-08-12T00:00:00Z'
            );
        """)

        await _public_agents_in_social_catalog_sqlite(conn)
        await _public_agents_in_social_catalog_sqlite(conn)
        rows = await conn.execute_fetchall("SELECT * FROM resource_social")

    assert len(rows) == 1
    assert rows[0]["resource_id"] == "agent-1"
    assert rows[0]["is_public"] == 1
    assert rows[0]["category"] == "Other"


async def test_migraciones_del_catalogo_viejo_no_fallan_sin_sus_tablas(tmp_path):
    """En una base nueva esas tablas ya no existen: deben ser no-ops."""
    from app.storage.migrations.steps.official import (
        _official_component_metadata_sqlite,
        _official_copy_mode_sqlite,
        _official_published_components_sqlite,
    )

    async with aiosqlite.connect(tmp_path / "fresh.db") as conn:
        conn.row_factory = sqlite3.Row
        await _official_component_metadata_sqlite(conn)
        await _official_copy_mode_sqlite(conn)
        await _official_published_components_sqlite(conn)

        tables = await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )

    assert tables == []


async def test_el_contenido_oficial_pasa_a_columnas_de_recurso(tmp_path):
    """La migración 7 marca los recursos y retira las tablas del catálogo."""
    from app.storage.migrations.steps.official import (
        _official_content_as_resources_sqlite,
    )

    async with aiosqlite.connect(tmp_path / "catalogo.db") as conn:
        conn.row_factory = sqlite3.Row
        for table in (
            "agents",
            "skills",
            "prompts",
            "tools",
            "knowledge_items",
            "agent_workflows",
        ):
            await conn.execute(
                f"CREATE TABLE {table} (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)"
            )
        await conn.execute(
            "CREATE TABLE official_packages ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL "
            "DEFAULT '', repository_url TEXT NOT NULL, repository_owner TEXT NOT NULL, "
            "repository_name TEXT NOT NULL, tracking_mode TEXT NOT NULL, "
            "tracking_ref TEXT NOT NULL, license TEXT NOT NULL DEFAULT '', "
            "latest_checked_at TEXT, last_sync_error TEXT, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        await conn.execute(
            "INSERT INTO official_packages VALUES ('pkg','Superpowers','',"
            "'https://github.com/obra/superpowers','obra','superpowers','release',"
            "'main','MIT',NULL,NULL,'2026-01-01','2026-01-01')"
        )
        await conn.execute("CREATE TABLE official_package_versions (package_id TEXT)")
        await conn.execute("CREATE TABLE official_package_components (package_id TEXT)")
        await conn.execute("CREATE TABLE official_package_copies (id TEXT)")

        await _official_content_as_resources_sqlite(conn)
        # Idempotente: el arranque siguiente vuelve a pasar por aquí.
        await _official_content_as_resources_sqlite(conn)

        skills = await conn.execute_fetchall("PRAGMA table_info(skills)")
        sources = await conn.execute_fetchall(
            "SELECT id, name, repository_url FROM official_sources"
        )
        tables = {
            str(row[0])
            for row in await conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {str(row[1]) for row in skills} >= {
        "official_source_id",
        "official_component_id",
    }
    assert [tuple(row) for row in sources] == [
        ("pkg", "Superpowers", "https://github.com/obra/superpowers")
    ]
    assert not tables & {
        "official_packages",
        "official_package_versions",
        "official_package_components",
        "official_package_copies",
    }
