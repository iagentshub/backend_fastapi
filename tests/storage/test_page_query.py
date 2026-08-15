from __future__ import annotations

import asyncio

from app.pagination.models import OffsetParams
from app.storage.db import open_db
from app.storage.page_query import fetch_offset_page


def test_fetch_offset_page_counts_and_decodes_only_requested_rows() -> None:
    async def run():
        async with open_db() as conn:
            await conn.execute(
                "CREATE TABLE page_query_test (id INTEGER PRIMARY KEY, name TEXT)"
            )
            await conn.executemany(
                "INSERT INTO page_query_test (id, name) VALUES (?, ?)",
                [(index, f"item-{index}") for index in range(1, 8)],
            )
            await conn.commit()
            decoded: list[int] = []

            def decode(row):
                decoded.append(int(row["id"]))
                return dict(row)

            page = await fetch_offset_page(
                conn,
                count_sql="SELECT COUNT(*) FROM page_query_test WHERE id > ?",
                select_sql=(
                    "SELECT id, name FROM page_query_test WHERE id > ? ORDER BY id ASC"
                ),
                params=(1,),
                page=OffsetParams(limit=2, offset=2),
                decode=decode,
            )
            return page, decoded

    page, decoded = asyncio.run(run())
    assert page.total == 6
    assert [item["id"] for item in page.items] == [4, 5]
    assert decoded == [4, 5]
    assert page.has_more is True
