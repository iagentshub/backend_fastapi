"""Índices y columnas derivadas para los catálogos cursor v2 adicionales."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.steps._columnas import (
    columna_existe_pg,
    columna_existe_sqlite,
)


async def _cursor_catalogs_sqlite(conn: Any) -> None:
    for column in ("component_type", "name", "source_path"):
        if not await columna_existe_sqlite(conn, "official_import_components", column):
            await conn.execute(
                "ALTER TABLE official_import_components "
                f"ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )
    await conn.execute(
        "UPDATE official_import_components SET "
        "component_type=COALESCE(json_extract(payload,'$.component_type'),''),"
        "name=COALESCE(json_extract(payload,'$.name'),''),"
        "source_path=COALESCE(json_extract(payload,'$.source_path'),'')"
    )
    await _cursor_indexes(conn)


async def _cursor_catalogs_pg(conn: Any) -> None:
    for column in ("component_type", "name", "source_path"):
        if not await columna_existe_pg(conn, "official_import_components", column):
            await conn.execute(
                "ALTER TABLE official_import_components "
                f"ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )
    await conn.execute(
        "UPDATE official_import_components SET "
        "component_type=COALESCE(payload::jsonb->>'component_type',''),"
        "name=COALESCE(payload::jsonb->>'name',''),"
        "source_path=COALESCE(payload::jsonb->>'source_path','')"
    )
    await _cursor_indexes(conn)


async def _cursor_indexes(conn: Any) -> None:
    await conn.execute("DROP INDEX IF EXISTS idx_rsoc_public_page")
    await conn.execute(
        "CREATE INDEX idx_rsoc_public_page ON resource_social("
        "is_public,updated_at DESC,stars_count DESC,resource_type,resource_id,owner)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_official_components_page ON "
        "official_import_components(draft_id,state,component_type,component_key)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_official_components_state_page ON "
        "official_import_components(draft_id,state,component_key)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_official_components_type_page ON "
        "official_import_components(draft_id,component_type,component_key)"
    )
