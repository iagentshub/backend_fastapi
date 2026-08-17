"""Immutable snapshots for editable hub resources."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.sql import sql
from app.storage.db import AsyncConn, open_db
from app.utils.generators import generate_date as _now
from app.utils.generators import generate_id


class ResourceVersionStorage:
    async def create(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        snapshot: Dict[str, Any],
        created_by: str,
        reason: str = "save",
        *,
        conn: Optional[AsyncConn] = None,
    ) -> Dict[str, Any]:
        async def write(target: AsyncConn) -> Dict[str, Any]:
            latest = await target.fetchval(
                sql("queries/resource_versions:max_version"),
                (resource_type, resource_id, owner_id),
            )
            version = int(latest or 0) + 1
            item = {
                "id": generate_id(32),
                "resource_type": resource_type,
                "resource_id": resource_id,
                "owner_id": owner_id,
                "version": version,
                "snapshot": snapshot,
                "created_by": created_by,
                "reason": reason,
                "created_at": _now(),
            }
            await target.execute(
                sql("queries/resource_versions:insert_version"),
                (
                    item["id"],
                    resource_type,
                    resource_id,
                    owner_id,
                    version,
                    json.dumps(snapshot, ensure_ascii=False),
                    created_by,
                    reason,
                    item["created_at"],
                ),
            )
            return item

        if conn is not None:
            return await write(conn)
        async with open_db() as own_conn:
            item = await write(own_conn)
            await own_conn.commit()
        return item

    async def list(
        self, resource_type: str, resource_id: str, owner_id: str
    ) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/resource_versions:list_versions"),
                (resource_type, resource_id, owner_id),
            )
        return [dict(row) for row in rows]

    async def get(
        self, resource_type: str, resource_id: str, owner_id: str, version: int
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/resource_versions:get_version"),
                (resource_type, resource_id, owner_id, version),
            )
        if not row:
            return None
        item = dict(row)
        item["snapshot"] = json.loads(item["snapshot"])
        return item
