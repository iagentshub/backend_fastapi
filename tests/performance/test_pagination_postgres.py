"""Benchmark real opt-in de offset frente a keyset en PostgreSQL.

Ejecución local:
    GAIA_TEST_PG_DSN=postgresql://postgres:test@127.0.0.1:55433/sqltest \
      pytest -q -s tests/performance/test_pagination_postgres.py
"""

from __future__ import annotations

import json
import os
import re

import pytest

from app.services.resource_visibility import build_visibility_filter

DSN = os.environ.get("GAIA_TEST_PG_DSN", "")


def _plan(value) -> dict:
    decoded = json.loads(value) if isinstance(value, str) else value
    return decoded[0]


def _max_scan_rows(node: dict) -> int:
    own = int(node.get("Actual Rows", 0)) if "Scan" in node.get("Node Type", "") else 0
    return max([own, *(_max_scan_rows(child) for child in node.get("Plans", []))])


def _relation_scan_rows(node: dict, relation: str) -> int:
    own = (
        int(node.get("Actual Rows", 0))
        if node.get("Relation Name") == relation
        else 0
    )
    return max(
        [
            own,
            *(
                _relation_scan_rows(child, relation)
                for child in node.get("Plans", [])
            ),
        ]
    )


@pytest.mark.skipif(
    not DSN,
    reason="define GAIA_TEST_PG_DSN para ejecutar el benchmark PostgreSQL",
)
@pytest.mark.asyncio
async def test_keyset_reads_a_bounded_page_while_offset_walks_the_prefix() -> None:
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "CREATE TEMP TABLE pagination_cursor_benchmark ("
            "id TEXT NOT NULL, owner_id TEXT NOT NULL, scope TEXT NOT NULL, "
            "updated_at BIGINT NOT NULL, PRIMARY KEY(id,owner_id))"
        )
        await conn.execute(
            "CREATE TEMP TABLE groups (id TEXT PRIMARY KEY,is_active SMALLINT NOT NULL)"
        )
        await conn.execute(
            "CREATE TEMP TABLE group_members ("
            "group_id TEXT NOT NULL,username TEXT NOT NULL,"
            "PRIMARY KEY(group_id,username))"
        )
        await conn.execute(
            "CREATE TEMP TABLE resource_group_shares ("
            "resource_type TEXT NOT NULL,resource_id TEXT NOT NULL,"
            "group_id TEXT NOT NULL,PRIMARY KEY(resource_type,resource_id,group_id))"
        )
        await conn.execute(
            "INSERT INTO groups VALUES ('shared-group',1),('inactive-group',0);"
            "INSERT INTO group_members VALUES ('shared-group','alice');"
            "INSERT INTO pagination_cursor_benchmark(id,owner_id,scope,updated_at) "
            "SELECT lpad(n::text,12,'0'),"
            "CASE WHEN n % 10 = 0 THEN 'another-owner' ELSE 'active-group' END,"
            "'private',n FROM generate_series(1,100000) n;"
            "INSERT INTO resource_group_shares(resource_type,resource_id,group_id) "
            "SELECT 'agent',lpad(n::text,12,'0'),'shared-group' "
            "FROM generate_series(10,100000,10) n"
        )
        await conn.execute(
            "CREATE INDEX benchmark_order_idx ON pagination_cursor_benchmark "
            "(updated_at DESC,id DESC);"
            "CREATE INDEX benchmark_members_user_idx ON group_members(username);"
            "CREATE INDEX benchmark_share_group_idx ON resource_group_shares"
            "(group_id,resource_type);"
            "ANALYZE pagination_cursor_benchmark;"
            "ANALYZE groups;ANALYZE group_members;ANALYZE resource_group_shares"
        )

        visibility = build_visibility_filter(
            alias="resource_row",
            user="alice",
            active_group_id="active-group",
            resource_type="agent",
            include_public=False,
        )
        parameter = 0

        def placeholder(_match: re.Match[str]) -> str:
            nonlocal parameter
            parameter += 1
            return f"${parameter}"

        where = re.sub(r"\?", placeholder, visibility.sql)
        base = (
            "FROM pagination_cursor_benchmark resource_row "
            f"WHERE {where}"
        )
        params = visibility.params

        boundary = await conn.fetchrow(
            f"SELECT resource_row.updated_at,resource_row.id {base} "
            "ORDER BY resource_row.updated_at DESC,resource_row.id DESC "
            "OFFSET 89999 LIMIT 1",
            *params,
        )
        offset_rows = await conn.fetch(
            f"SELECT resource_row.id {base} "
            "ORDER BY resource_row.updated_at DESC,resource_row.id DESC "
            "OFFSET 90000 LIMIT 50",
            *params,
        )
        position_one = len(params) + 1
        position_two = len(params) + 2
        keyset_rows = await conn.fetch(
            f"SELECT resource_row.id {base} AND "
            f"(resource_row.updated_at,resource_row.id) < "
            f"(${position_one},${position_two}) "
            "ORDER BY resource_row.updated_at DESC,resource_row.id DESC LIMIT 50",
            *params,
            boundary["updated_at"],
            boundary["id"],
        )
        assert [row["id"] for row in keyset_rows] == [row["id"] for row in offset_rows]

        offset_plan = _plan(
            await conn.fetchval(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "
                f"SELECT resource_row.id {base} "
                "ORDER BY resource_row.updated_at DESC,resource_row.id DESC "
                "OFFSET 90000 LIMIT 50",
                *params,
            )
        )
        keyset_plan = _plan(
            await conn.fetchval(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "
                f"SELECT resource_row.id {base} AND "
                f"(resource_row.updated_at,resource_row.id) < "
                f"(${position_one},${position_two}) "
                "ORDER BY resource_row.updated_at DESC,resource_row.id DESC LIMIT 50",
                *params,
                boundary["updated_at"],
                boundary["id"],
            )
        )

        offset_scanned = _relation_scan_rows(
            offset_plan["Plan"], "pagination_cursor_benchmark"
        )
        keyset_scanned = _relation_scan_rows(
            keyset_plan["Plan"], "pagination_cursor_benchmark"
        )
        print(
            "pagination-postgres",
            {
                "offset_ms": offset_plan["Execution Time"],
                "keyset_ms": keyset_plan["Execution Time"],
                "offset_scan_rows": offset_scanned,
                "keyset_scan_rows": keyset_scanned,
                "offset_max_aux_scan_rows": _max_scan_rows(offset_plan["Plan"]),
                "keyset_max_aux_scan_rows": _max_scan_rows(keyset_plan["Plan"]),
            },
        )
        assert offset_scanned >= 90_000
        assert keyset_scanned <= 100
        assert keyset_plan["Execution Time"] < offset_plan["Execution Time"]
    finally:
        await conn.close()
