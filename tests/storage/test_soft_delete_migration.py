"""Verifica que la migración añade is_active/deactivated_at y resource_labels."""

from __future__ import annotations

import asyncio

from app.storage.db import open_db
from app.storage.db_migrations import _RESOURCE_TABLES


def _columns(table: str) -> set[str]:
    async def _run() -> set[str]:
        async with open_db() as conn:
            rows = await conn.fetchall(f"PRAGMA table_info({table})")
            return {row[1] for row in rows}

    return asyncio.run(_run())


def _column_types(table: str) -> dict[str, str]:
    async def _run() -> dict[str, str]:
        async with open_db() as conn:
            rows = await conn.fetchall(f"PRAGMA table_info({table})")
            return {row[1]: row[2] for row in rows}

    return asyncio.run(_run())


def test_resource_tables_have_soft_delete_columns(patch_data_dir):  # noqa: ARG001
    for table in _RESOURCE_TABLES:
        cols = _columns(table)
        assert "is_active" in cols, f"{table} sin is_active"
        assert "deactivated_at" in cols, f"{table} sin deactivated_at"
        assert _column_types(table)["is_active"] == "INTEGER"


def test_reusable_content_has_global_active_state(patch_data_dir):  # noqa: ARG001
    for table in ("skills", "prompts", "tools"):
        cols = _columns(table)
        assert "is_active" in cols
        assert "deactivated_at" in cols
        assert _column_types(table)["is_active"] == "INTEGER"


def test_resource_labels_table_exists(patch_data_dir):  # noqa: ARG001
    async def _run() -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='resource_labels'"
            )
            return row is not None

    assert asyncio.run(_run())


def test_groups_use_single_integer_active_flag(patch_data_dir):  # noqa: ARG001
    types = _column_types("groups")
    assert types["is_active"] == "INTEGER"
    assert "status" not in types
    assert "deactivated_at" not in types


def test_date_fixups_applied(patch_data_dir):  # noqa: ARG001
    assert "updated_at" in _columns("groups")
    assert "created_at" in _columns("memory_files")
