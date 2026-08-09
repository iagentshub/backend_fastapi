from __future__ import annotations

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


async def test_official_published_components_column_is_added_on_old_dbs(tmp_path):
    """La selección publicada se añade a versiones creadas antes de existir."""
    from app.storage.migrations.sqlite import _official_published_components

    async with aiosqlite.connect(tmp_path / "official.db") as conn:
        conn.row_factory = sqlite3.Row
        await conn.execute(
            "CREATE TABLE official_package_versions ("
            "package_id TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL)"
        )
        await conn.execute(
            "INSERT INTO official_package_versions VALUES ('pkg','v1','published')"
        )

        await _official_published_components(conn)
        # Idempotente: la segunda pasada no debe fallar ni duplicar la columna.
        await _official_published_components(conn)

        rows = await conn.execute_fetchall(
            "PRAGMA table_info(official_package_versions)"
        )
        columns = [str(row[1]) for row in rows]
        stored = await conn.execute_fetchall(
            "SELECT published_components FROM official_package_versions"
        )

    assert columns.count("published_components") == 1
    assert stored[0][0] == "[]"


async def test_migraciones_del_catalogo_viejo_no_fallan_sin_sus_tablas(tmp_path):
    """En una base nueva esas tablas ya no existen: deben ser no-ops."""
    from app.storage.migrations.sqlite import (
        _official_component_metadata,
        _official_copy_mode,
        _official_published_components,
    )

    async with aiosqlite.connect(tmp_path / "fresh.db") as conn:
        conn.row_factory = sqlite3.Row
        await _official_component_metadata(conn)
        await _official_copy_mode(conn)
        await _official_published_components(conn)

        tables = await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )

    assert tables == []


async def test_el_contenido_oficial_pasa_a_columnas_de_recurso(tmp_path):
    """La migración 7 marca los recursos y retira las tablas del catálogo."""
    from app.storage.migrations.sqlite import _official_content_as_resources

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

        await _official_content_as_resources(conn)
        # Idempotente: el arranque siguiente vuelve a pasar por aquí.
        await _official_content_as_resources(conn)

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
