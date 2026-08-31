"""Cota estructural: una página no materializa el catálogo completo."""

from __future__ import annotations

import pytest

from app.services.resource_visibility import build_visibility_filter
from app.storage.db import open_db


@pytest.mark.asyncio
async def test_scoped_resource_page_uses_order_index_without_temp_sort() -> None:
    """La ruta visible debe recorrer el índice ya ordenado, no ordenar la tabla."""

    async with open_db() as conn:
        await conn.executemany(
            "INSERT INTO agents ("
            "id,owner_id,name,scope,data,is_active,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    f"plan-{index:05d}",
                    "plan-user" if index % 4 == 0 else f"other-{index % 7}",
                    f"Agent {index}",
                    "private",
                    "{}",
                    1,
                    f"2026-08-01T00:{index % 60:02d}:00Z",
                    f"2026-08-{1 + index % 28:02d}T{index % 24:02d}:00:00Z",
                )
                for index in range(10_000)
            ],
        )
        await conn.commit()
        visibility = build_visibility_filter(
            alias="resource_row",
            user="plan-user",
            active_group_id="plan-user",
            resource_type="agent",
            include_public=False,
        )
        plan = await conn.fetchall(
            "EXPLAIN QUERY PLAN SELECT resource_row.id FROM agents resource_row "
            f"WHERE ({visibility.sql}) AND resource_row.is_active = 1 "
            "AND (resource_row.updated_at, resource_row.id) < (?, ?) "
            "ORDER BY resource_row.updated_at DESC, resource_row.id DESC "
            "LIMIT ?",
            (
                *visibility.params,
                "2026-08-15T12:00:00Z",
                "plan-05000",
                51,
            ),
        )

    details = "\n".join(str(row[3]) for row in plan)
    assert "idx_agents_visible_order" in details
    assert "SEARCH resource_row" in details
    assert "(updated_at,id)<(?,?)" in details
    assert "USE TEMP B-TREE FOR ORDER BY" not in details
