"""Persistence for visual multi-agent workflow definitions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStorage:
    async def list(self, owner_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM agent_workflows WHERE owner_id=? ORDER BY updated_at DESC",
                (owner_id,),
            )
        return [self._decode(row) for row in rows]

    async def get(self, workflow_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM agent_workflows WHERE id=? AND owner_id=?",
                (workflow_id, owner_id),
            )
        return self._decode(row) if row else None

    async def get_any(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM agent_workflows WHERE id=? ORDER BY updated_at DESC LIMIT 1",
                (workflow_id,),
            )
        return self._decode(row) if row else None

    async def list_by_ids(self, workflow_ids: List[str]) -> List[Dict[str, Any]]:
        if not workflow_ids:
            return []
        placeholders = ",".join("?" for _ in workflow_ids)
        async with open_db() as conn:
            rows = await conn.fetchall(
                f"SELECT * FROM agent_workflows WHERE id IN ({placeholders}) "
                "ORDER BY updated_at DESC",
                tuple(workflow_ids),
            )
        return [self._decode(row) for row in rows]

    async def save(
        self, owner_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        workflow_id = str(payload.get("id") or uuid4().hex[:12])
        existing = await self.get(workflow_id, owner_id)
        now = _now()
        item = {
            "id": workflow_id,
            "owner_id": owner_id,
            "name": str(payload["name"]).strip(),
            "description": str(payload.get("description") or "").strip(),
            "definition": payload["definition"],
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO agent_workflows "
                "(id, owner_id, name, description, definition, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id, owner_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, "
                "definition=excluded.definition, updated_at=excluded.updated_at",
                (
                    item["id"],
                    owner_id,
                    item["name"],
                    item["description"],
                    json.dumps(item["definition"], ensure_ascii=False),
                    item["created_at"],
                    item["updated_at"],
                ),
            )
            await conn.commit()
        return item

    async def delete(self, workflow_id: str, owner_id: str) -> bool:
        async with open_db() as conn:
            existing = await conn.fetchval(
                "SELECT 1 FROM agent_workflows WHERE id=? AND owner_id=?",
                (workflow_id, owner_id),
            )
            if not existing:
                return False
            await conn.execute(
                "DELETE FROM agent_workflows WHERE id=? AND owner_id=?",
                (workflow_id, owner_id),
            )
            await conn.execute(
                "DELETE FROM resource_workspace_shares "
                "WHERE resource_type='workflow' AND resource_id=?",
                (workflow_id,),
            )
            await conn.commit()
        return True

    @staticmethod
    def _decode(row: Any) -> Dict[str, Any]:
        item = dict(row)
        item["definition"] = json.loads(item["definition"])
        return item
