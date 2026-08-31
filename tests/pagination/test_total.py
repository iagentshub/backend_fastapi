from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.pagination.metrics import reset_for_tests, snapshot
from app.pagination.total import ExactTotalTimeout, exact_total
from app.storage.db import AsyncConn


def test_exact_total_reuses_signed_cursor_value_without_query() -> None:
    conn = AsyncMock()
    total, from_cursor = asyncio.run(
        exact_total(
            conn,
            sql="SELECT COUNT(*) FROM agents",
            params=(),
            resource="agent",
            cursor_total=81,
        )
    )
    assert (total, from_cursor) == (81, True)
    conn.fetchval_with_timeout.assert_not_awaited()


def test_exact_total_has_a_bounded_timeout() -> None:
    reset_for_tests()

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(0.02)
        raise TimeoutError

    conn = AsyncMock()
    conn.fetchval_with_timeout.side_effect = slow
    with patch("app.pagination.total.EXACT_TOTAL_TIMEOUT_SECONDS", 0.001):
        with pytest.raises(ExactTotalTimeout):
            asyncio.run(
                exact_total(
                    conn,
                    sql="SELECT COUNT(*) FROM agents",
                    params=(),
                    resource="agent",
                    cursor_total=None,
                )
            )
    assert snapshot()["agent"]["total_timeouts"] == 1


def test_exact_totals_do_not_occupy_the_whole_pool_concurrently() -> None:
    class CountingConn:
        active = 0
        maximum = 0

        async def fetchval_with_timeout(self, *_args, **_kwargs):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return 3

    async def run() -> int:
        conn = CountingConn()
        await asyncio.gather(
            *(
                exact_total(
                    conn,  # type: ignore[arg-type]
                    sql="SELECT COUNT(*) FROM agents",
                    params=(),
                    resource="agent",
                    cursor_total=None,
                )
                for _ in range(3)
            )
        )
        return conn.maximum

    assert asyncio.run(run()) == 1


def test_sqlite_timeout_interrupts_the_query_before_reusing_connection() -> None:
    async def run() -> float:
        import aiosqlite

        raw = await aiosqlite.connect(":memory:")
        conn = AsyncConn(raw, is_pg=False)
        try:
            with pytest.raises(TimeoutError):
                await conn.fetchval_with_timeout(
                    "WITH RECURSIVE counter(value) AS ("
                    "SELECT 1 UNION ALL SELECT value + 1 FROM counter "
                    "WHERE value < 100000000) SELECT sum(value) FROM counter",
                    timeout=0.01,
                )
            started = time.perf_counter()
            assert await conn.fetchval("SELECT 1") == 1
            return time.perf_counter() - started
        finally:
            await raw.close()

    assert asyncio.run(run()) < 0.1
