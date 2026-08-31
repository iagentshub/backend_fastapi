"""Indices finales para los listados cursor que sustituyen contratos offset."""

from __future__ import annotations

from typing import Any


async def _cursor_completion_indexes(conn: Any) -> None:
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_rsoc_feed_page ON resource_social("
        "is_public,updated_at DESC,resource_type,resource_id,owner)",
        "CREATE INDEX IF NOT EXISTS idx_connections_updated_page ON "
        "connections(updated_at DESC,id)",
        "CREATE INDEX IF NOT EXISTS idx_llm_orchestrations_updated_page ON "
        "llm_orchestrations(updated_at DESC,id)",
    )
    for statement in statements:
        await conn.execute(statement)
