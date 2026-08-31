"""Persistence for visual multi-agent workflow definitions."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.sql import sql
from app.storage.db import AsyncConn, open_db
from app.storage.resource_base import ResourceStorage
from app.storage.skill_storage import SKILL_LABELS, ensure_origin_label
from app.utils.generators import generate_date as _now
from app.utils.generators import generate_id


class WorkflowStorage(ResourceStorage):
    # La tabla se llama agent_workflows; el resource_type canónico es "workflow".
    table = "agent_workflows"
    resource_type = "workflow"

    async def list(self, owner_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/workflows:list_by_owner"),
                (owner_id,),
            )
        return [self._decode(row) for row in rows]

    async def get(self, workflow_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/workflows:get_owned"),
                (workflow_id, owner_id),
            )
        return self._decode(row) if row else None

    async def get_any(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/workflows:get_any"),
                (workflow_id,),
            )
        return self._decode(row) if row else None

    def decode_row(self, row: Any) -> Dict[str, Any]:
        """Decodifica una fila de esta tabla para quien la pagine por
        otra vía —el panel de administración lista sin filtro de
        visibilidad—, sin reescribir la decodificación por segunda vez."""
        return self._decode(row)

    async def list_all(self) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/workflows:list_all")
            )
        return [self._decode(row) for row in rows]

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

    async def list_by_ids_with_active_owner(
        self, workflow_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Lista por lote excluyendo recursos de grupos propietarios inactivos."""
        if not workflow_ids:
            return []
        placeholders = ",".join("?" for _ in workflow_ids)
        async with open_db() as conn:
            rows = await conn.fetchall(
                f"SELECT w.* FROM agent_workflows w "
                f"LEFT JOIN groups g ON g.id = w.owner_id "
                f"WHERE w.id IN ({placeholders}) "
                f"AND (g.id IS NULL OR g.is_active = 1) "
                f"ORDER BY w.updated_at DESC",
                tuple(workflow_ids),
            )
        return [self._decode(row) for row in rows]

    async def save(
        self,
        owner_id: str,
        payload: Dict[str, Any],
        *,
        conn: Optional[AsyncConn] = None,
        assume_new: bool = False,
    ) -> Dict[str, Any]:
        workflow_id = str(payload.get("id") or generate_id())
        existing = None if assume_new else await self.get(workflow_id, owner_id)
        now = _now()
        labels = [str(lbl) for lbl in (payload.get("labels") or ["private"]) if lbl]
        invalid_labels = [label for label in labels if label not in SKILL_LABELS]
        if invalid_labels:
            raise ValueError("invalid workflow labels")
        labels = ensure_origin_label(labels)
        item = {
            "id": workflow_id,
            "resource_type": "workflow",
            "owner_id": owner_id,
            "name": str(payload["name"]).strip(),
            "description": str(payload.get("description") or "").strip(),
            "definition": payload["definition"],
            "scope": str(payload.get("scope") or "private"),
            "labels": labels,
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
            "is_active": bool(existing.get("is_active", True)) if existing else True,
        }

        async def write(target: AsyncConn) -> None:
            await target.execute(
                sql("queries/workflows:upsert"),
                (
                    item["id"],
                    owner_id,
                    item["name"],
                    item["description"],
                    json.dumps(item["definition"], ensure_ascii=False),
                    item["scope"],
                    json.dumps(item["labels"], ensure_ascii=False),
                    item["created_at"],
                    item["updated_at"],
                ),
            )

        if conn is not None:
            await write(conn)
            await self.sync_labels(workflow_id, owner_id, labels, conn=conn)
        else:
            async with open_db() as own_conn:
                await write(own_conn)
                await own_conn.commit()
            await self.sync_labels(workflow_id, owner_id, labels)
        return item

    async def delete(self, workflow_id: str, owner_id: str) -> bool:
        async with open_db() as conn:
            existing = await conn.fetchval(
                sql("queries/workflows:exists_owned"),
                (workflow_id, owner_id),
            )
            if not existing:
                return False
            await conn.execute(
                sql("queries/workflows:delete_owned"),
                (workflow_id, owner_id),
            )
            await conn.execute(
                sql("queries/workflows:delete_shares"),
                (workflow_id,),
            )
            await conn.commit()
        await self.clear_labels(workflow_id)
        return True

    async def delete_any(self, workflow_id: str) -> bool:
        async with open_db() as conn:
            existing = await conn.fetchval(
                sql("queries/workflows:exists_any"), (workflow_id,)
            )
            if not existing:
                return False
            await conn.execute(sql("queries/workflows:delete_any"), (workflow_id,))
            await conn.execute(
                sql("queries/workflows:delete_shares"),
                (workflow_id,),
            )
            await conn.commit()
        await self.clear_labels(workflow_id)
        return True

    @staticmethod
    def _decode(row: Any) -> Dict[str, Any]:
        item = dict(row)
        item["resource_type"] = "workflow"
        item["definition"] = json.loads(item["definition"])
        try:
            item["labels"] = json.loads(item.get("labels") or '["private"]')
        except (json.JSONDecodeError, TypeError):
            item["labels"] = ["private"]
        if "is_active" in item:
            item["is_active"] = bool(item["is_active"])
        return item
