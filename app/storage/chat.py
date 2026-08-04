"""Chat history storage — SQLite/PostgreSQL conversations and messages."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.db import open_db
from app.utils import now_iso as _now
from app.utils.generators import generate_id


class ChatStorage:
    # db_path se acepta y se ignora: la conexión la abre open_db() con la
    # config global. Ver el comentario largo en storage.py.
    def __init__(self, db_path: Path) -> None:
        pass

    # ── Conversations ──────────────────────────────────────────────────────────

    # Total de tokens por conversación (suma de sus mensajes) — para mostrar
    # consumo por chat en la lista de conversaciones sin traer los mensajes.
    _CONVERSATION_TOKENS_SELECT = (
        "SELECT c.id, c.user_id, c.agent_id, c.title, c.created_at, c.updated_at, "
        "COALESCE(SUM(m.tokens_in), 0) AS tokens_in, "
        "COALESCE(SUM(m.tokens_out), 0) AS tokens_out "
        "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
    )
    _CONVERSATION_TOKENS_GROUP_BY = (
        "GROUP BY c.id, c.user_id, c.agent_id, c.title, c.created_at, c.updated_at "
    )

    async def list_conversations(
        self, user_id: str, agent_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                self._CONVERSATION_TOKENS_SELECT
                + "WHERE c.user_id = ? AND c.agent_id = ? "
                + self._CONVERSATION_TOKENS_GROUP_BY
                + "ORDER BY c.updated_at DESC LIMIT ?",
                (user_id, agent_id, limit),
            )
            return [dict(r) for r in rows]

    async def list_recent_conversations(
        self, user_id: str, limit: int = 8
    ) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                self._CONVERSATION_TOKENS_SELECT
                + "WHERE c.user_id = ? "
                + self._CONVERSATION_TOKENS_GROUP_BY
                + "ORDER BY c.updated_at DESC LIMIT ?",
                (user_id, limit),
            )
            return [dict(r) for r in rows]

    async def new_conversation(
        self, user_id: str, agent_id: str, title: str = ""
    ) -> Dict[str, Any]:
        conv_id = generate_id(32)
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
        self,
        conv_id: str,
        role: str,
        content: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> Dict[str, Any]:
        msg_id = generate_id(32)
        now = _now()
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO messages "
                "(id, conversation_id, role, content, tokens_in, tokens_out, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (msg_id, conv_id, role, content, tokens_in, tokens_out, now),
            )
            await conn.commit()
        return {
            "id": msg_id,
            "conversation_id": conv_id,
            "role": role,
            "content": content,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "created_at": now,
        }

    async def get_messages(
        self, conv_id: str, user_id: str, limit: int = 200
    ) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT m.id, m.role, m.content, m.tokens_in, m.tokens_out, m.created_at "
                "FROM messages m "
                "WHERE m.conversation_id = ? "
                "  AND EXISTS (SELECT 1 FROM conversations c "
                "              WHERE c.id = ? AND c.user_id = ?) "
                "ORDER BY m.created_at ASC LIMIT ?",
                (conv_id, conv_id, user_id, limit),
            )
            return [dict(r) for r in rows]
