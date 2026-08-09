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
