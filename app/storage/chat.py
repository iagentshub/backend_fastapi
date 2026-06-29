"""Chat history storage — SQLite/PostgreSQL conversations and messages."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStorage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    # ── Conversations ──────────────────────────────────────────────────────────

    async def list_conversations(
        self, user_id: str, agent_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT id, user_id, agent_id, title, created_at, updated_at "
                "FROM conversations WHERE user_id = ? AND agent_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (user_id, agent_id, limit),
            )
            return [dict(r) for r in rows]

    async def new_conversation(
        self, user_id: str, agent_id: str, title: str = ""
    ) -> Dict[str, Any]:
        conv_id = uuid4().hex
        now = _now()
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO conversations (id, user_id, agent_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conv_id, user_id, agent_id, title or "", now, now),
            )
            await conn.commit()
        return {
            "id": conv_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }

    async def get_conversation(
        self, conv_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT id, user_id, agent_id, title, created_at, updated_at "
                "FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id),
            )
            return dict(row) if row else None

    async def touch_conversation(self, conv_id: str, title: str = "") -> None:
        """Update updated_at; set title only if it was empty."""
        now = _now()
        async with open_db() as conn:
            if title:
                await conn.execute(
                    "UPDATE conversations "
                    "SET updated_at = ?, title = CASE WHEN title = '' THEN ? ELSE title END "
                    "WHERE id = ?",
                    (now, title, conv_id),
                )
            else:
                await conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, conv_id),
                )
            await conn.commit()

    async def delete_conversation(self, conv_id: str, user_id: str) -> bool:
        async with open_db() as conn:
            exists = await conn.fetchone(
                "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id),
            )
            if not exists:
                return False
            await conn.execute(
                "DELETE FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id),
            )
            await conn.commit()
            return True

    # ── Messages ───────────────────────────────────────────────────────────────

    async def add_message(
        self, conv_id: str, role: str, content: str
    ) -> Dict[str, Any]:
        msg_id = uuid4().hex
        now = _now()
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (msg_id, conv_id, role, content, now),
            )
            await conn.commit()
        return {
            "id": msg_id,
            "conversation_id": conv_id,
            "role": role,
            "content": content,
            "created_at": now,
        }

    async def get_messages(
        self, conv_id: str, user_id: str, limit: int = 200
    ) -> List[Dict[str, Any]]:
        if not await self.get_conversation(conv_id, user_id):
            return []
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT id, role, content, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
                (conv_id, limit),
            )
            return [dict(r) for r in rows]
