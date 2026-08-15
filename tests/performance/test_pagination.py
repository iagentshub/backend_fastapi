"""Cota estructural: una página no materializa el catálogo completo."""

from __future__ import annotations

import pytest

from app.pagination.models import OffsetParams
from app.storage.db import open_db
from app.storage.page_query import fetch_offset_page


@pytest.mark.asyncio
async def test_ten_thousand_rows_decode_only_requested_page() -> None:
    async with open_db() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS pagination_probe "
            "(id TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        await conn.execute("DELETE FROM pagination_probe")
        await conn.executemany(
            "INSERT INTO pagination_probe(id,value) VALUES (?,?)",
            [(f"id-{index:05d}", f"value-{index}") for index in range(10_000)],
        )
        await conn.commit()

        decoded = 0

        def decode(row):
            nonlocal decoded
            decoded += 1
            return dict(row)

        page = await fetch_offset_page(
            conn,
            count_sql="SELECT COUNT(*) FROM pagination_probe",
            select_sql="SELECT id,value FROM pagination_probe ORDER BY id",
            params=(),
            page=OffsetParams(limit=50, offset=9_900),
            decode=decode,
        )

    assert page.total == 10_000
    assert len(page.items) == 50
    assert decoded == 50
