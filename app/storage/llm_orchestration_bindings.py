"""Private per-user connection bindings for shared LLM orchestrations."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.storage.db import open_db
from app.utils.generators import generate_date


class LLMOrchestrationBindingStorage:
    async def get(
        self, orchestration_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM llm_orchestration_bindings "
                "WHERE orchestration_id=? AND user_id=?",
                (orchestration_id, user_id),
            )
        if not row:
            return None
        item = dict(row)
        item.update(json.loads(item.pop("definition")))
        return item

    async def save(
        self, orchestration_id: str, user_id: str, definition: Dict[str, Any]
    ) -> Dict[str, Any]:
        existing = await self.get(orchestration_id, user_id)
        now = generate_date()
        created_at = existing["created_at"] if existing else now
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO llm_orchestration_bindings "
                "(orchestration_id, user_id, definition, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(orchestration_id, user_id) DO UPDATE SET "
                "definition=excluded.definition, updated_at=excluded.updated_at",
                (
                    orchestration_id,
                    user_id,
                    json.dumps(definition, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
            await conn.commit()
        return {
            "orchestration_id": orchestration_id,
            "user_id": user_id,
            **definition,
            "created_at": created_at,
            "updated_at": now,
        }

    async def delete_for_orchestration(self, orchestration_id: str) -> None:
        async with open_db() as conn:
            await conn.execute(
                "DELETE FROM llm_orchestration_bindings WHERE orchestration_id=?",
                (orchestration_id,),
            )
            await conn.commit()
