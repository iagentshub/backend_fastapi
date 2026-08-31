"""Persistencia de orquestaciones de conexiones LLM."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.sql import sql
from app.storage.db import open_db
from app.storage.resource_base import ResourceStorage
from app.utils.generators import generate_date, generate_id


class LLMOrchestrationStorage(ResourceStorage):
    table = "llm_orchestrations"
    resource_type = "llm_orchestration"

    @staticmethod
    def _decode(row: Any) -> Dict[str, Any]:
        item = dict(row)
        definition = json.loads(item.pop("definition"))
        item.update(definition)
        item["resource_type"] = "llm_orchestration"
        item["scope"] = "private"
        item["is_active"] = bool(item.get("is_active", True))
        try:
            item["labels"] = json.loads(item.get("labels") or '["private"]')
        except (json.JSONDecodeError, TypeError):
            item["labels"] = ["private"]
        return item

    async def list(self, owner_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/llm_orchestrations:list_by_owner"),
                (owner_id,),
            )
        return [self._decode(row) for row in rows]

    def decode_row(self, row: Any) -> Dict[str, Any]:
        """Decodifica una fila de esta tabla para quien la pagine por
        otra vía —el panel de administración lista sin filtro de
        visibilidad—, sin reescribir la decodificación por segunda vez."""
        return self._decode(row)

    async def list_all(self) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/llm_orchestrations:list_all")
            )
        return [self._decode(row) for row in rows]

    async def get(self, item_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/llm_orchestrations:get_owned"),
                (item_id, owner_id),
            )
        return self._decode(row) if row else None

    async def get_any(self, item_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/llm_orchestrations:get_any"),
                (item_id,),
            )
        return self._decode(row) if row else None

    async def save(self, owner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item_id = str(payload.get("id") or generate_id())
        existing = await self.get(item_id, owner_id)
        now = generate_date()
        definition = {
            "mode": payload["mode"],
            "candidates": payload["candidates"],
            "router_connection_id": payload.get("router_connection_id"),
        }
        labels = [str(value) for value in (payload.get("labels") or ["private"])]
        item = {
            "id": item_id,
            "resource_type": "llm_orchestration",
            "owner_id": owner_id,
            "name": str(payload["name"]).strip(),
            "description": str(payload.get("description") or "").strip(),
            **definition,
            "scope": "private",
            "labels": labels,
            "is_active": bool(existing.get("is_active", True)) if existing else True,
            "deactivated_at": existing.get("deactivated_at") if existing else None,
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        async with open_db() as conn:
            await conn.execute(
                sql("queries/llm_orchestrations:upsert"),
                (
                    item_id,
                    owner_id,
                    item["name"],
                    item["description"],
                    json.dumps(definition, ensure_ascii=False),
                    json.dumps(labels, ensure_ascii=False),
                    1 if item["is_active"] else 0,
                    item["deactivated_at"],
                    item["created_at"],
                    now,
                ),
            )
            await conn.commit()
        await self.sync_labels(item_id, owner_id, labels)
        return item

    async def delete(self, item_id: str, owner_id: str) -> bool:
        async with open_db() as conn:
            found = await conn.fetchval(
                sql("queries/llm_orchestrations:exists_owned"),
                (item_id, owner_id),
            )
            if not found:
                return False
            await conn.execute(
                sql("queries/llm_orchestrations:delete_owned"),
                (item_id, owner_id),
            )
            await conn.execute(
                sql("queries/llm_orchestrations:delete_bindings"),
                (item_id,),
            )
            await conn.commit()
        await self.clear_labels(item_id)
        return True

    async def delete_any(self, item_id: str) -> bool:
        async with open_db() as conn:
            found = await conn.fetchval(
                sql("queries/llm_orchestrations:exists_any"), (item_id,)
            )
            if not found:
                return False
            await conn.execute(sql("queries/llm_orchestrations:delete_any"), (item_id,))
            await conn.execute(
                sql("queries/llm_orchestrations:delete_bindings"),
                (item_id,),
            )
            await conn.execute(
                sql("queries/llm_orchestrations:delete_shares"),
                (item_id,),
            )
            await conn.commit()
        await self.clear_labels(item_id)
        return True
