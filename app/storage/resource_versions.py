"""Immutable snapshots for editable hub resources."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResourceVersionStorage:
    async def create(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        snapshot: Dict[str, Any],
        created_by: str,
        reason: str = "save",
    ) -> Dict[str, Any]:
        async with open_db() as conn:
            latest = await conn.fetchval(
                "SELECT MAX(version) FROM resource_versions "
                "WHERE resource_type=? AND resource_id=? AND owner_id=?",
                (resource_type, resource_id, owner_id),
            )
            version = int(latest or 0) + 1
            item = {
                "id": uuid4().hex,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "owner_id": owner_id,
                "version": version,
                "snapshot": snapshot,
                "created_by": created_by,
                "reason": reason,
                "created_at": _now(),
            }
            await conn.execute(
                "INSERT INTO resource_versions "
                "(id, resource_type, resource_id, owner_id, version, snapshot, "
                "created_by, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            await conn.commit()
        return item

    async def list(
        self, resource_type: str, resource_id: str, owner_id: str
    ) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT id, version, created_by, reason, created_at "
                "FROM resource_versions WHERE resource_type=? AND resource_id=? "
                "AND owner_id=? ORDER BY version DESC",
                (resource_type, resource_id, owner_id),
            )
        return [dict(row) for row in rows]

    async def get(
        self, resource_type: str, resource_id: str, owner_id: str, version: int
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM resource_versions WHERE resource_type=? "
                "AND resource_id=? AND owner_id=? AND version=?",
                (resource_type, resource_id, owner_id, version),
            )
        if not row:
            return None
        item = dict(row)
        item["snapshot"] = json.loads(item["snapshot"])
        return item
