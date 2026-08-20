"""Chat history storage — SQLite/PostgreSQL conversations and messages."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.pagination.cursor import decode_cursor, encode_cursor
from app.pagination.models import CursorPage, CursorPosition
from app.sql import sql
from app.storage.db import open_db
from app.utils import now_iso as _now
from app.utils.generators import generate_id


class ChatStorage:
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

    async def list_conversations_page(
        self,
        user_id: str,
        agent_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> CursorPage[Dict[str, Any]]:
        """Página estable de conversaciones, de más reciente a más antigua."""
        position = decode_cursor(cursor) if cursor else None
        cursor_where = ""
        params: list[Any] = [user_id, agent_id]
        if position is not None:
            cursor_where = "AND (c.updated_at < ? OR (c.updated_at = ? AND c.id < ?)) "
            params.extend([position.created_at, position.created_at, position.item_id])
        params.append(limit + 1)
        async with open_db() as conn:
            rows = await conn.fetchall(
                self._CONVERSATION_TOKENS_SELECT
                + "WHERE c.user_id = ? AND c.agent_id = ? "
                + cursor_where
                + self._CONVERSATION_TOKENS_GROUP_BY
                + "ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
                tuple(params),
            )
        items = [dict(row) for row in rows[:limit]]
        has_more = len(rows) > limit
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(
                CursorPosition(str(last["updated_at"]), str(last["id"]))
            )
        return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)

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
                sql("queries/chat:insert_conversation"),
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
                sql("queries/chat:get_conversation"),
                (conv_id, user_id),
            )
            return dict(row) if row else None

    async def touch_conversation(self, conv_id: str, title: str = "") -> None:
        """Update updated_at; set title only if it was empty."""
        now = _now()
        async with open_db() as conn:
            if title:
                await conn.execute(
                    sql("queries/chat:touch_conversation_with_title"),
                    (now, title, conv_id),
                )
            else:
                await conn.execute(
                    sql("queries/chat:touch_conversation"),
                    (now, conv_id),
                )
            await conn.commit()

    async def delete_conversation(self, conv_id: str, user_id: str) -> bool:
        async with open_db() as conn:
            exists = await conn.fetchone(
                sql("queries/chat:conversation_exists"),
                (conv_id, user_id),
            )
            if not exists:
                return False
            await conn.execute(
                sql("queries/chat:delete_conversation"),
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
        interrupted: bool = False,
        usage_estimated: bool = False,
    ) -> Dict[str, Any]:
        msg_id = generate_id(32)
        now = _now()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/chat:insert_message"),
                (
                    msg_id,
                    conv_id,
                    role,
                    content,
                    tokens_in,
                    tokens_out,
                    interrupted,
                    usage_estimated,
                    now,
                ),
            )
            await conn.commit()
        return {
            "id": msg_id,
            "conversation_id": conv_id,
            "role": role,
            "content": content,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "interrupted": interrupted,
            "usage_estimated": usage_estimated,
            "created_at": now,
        }

    async def get_messages_page(
        self,
        conv_id: str,
        user_id: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> CursorPage[Dict[str, Any]]:
        """Carga hacia atrás y devuelve cada página en orden cronológico."""
        position = decode_cursor(cursor) if cursor else None
        cursor_where = ""
        params: list[Any] = [conv_id, conv_id, user_id]
        if position is not None:
            cursor_where = "AND (m.created_at < ? OR (m.created_at = ? AND m.id < ?)) "
            params.extend([position.created_at, position.created_at, position.item_id])
        params.append(limit + 1)
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT m.id, m.role, m.content, m.tokens_in, m.tokens_out, "
                "m.interrupted, m.usage_estimated, m.created_at "
                "FROM messages m WHERE m.conversation_id = ? "
                "AND EXISTS (SELECT 1 FROM conversations c "
                "WHERE c.id = ? AND c.user_id = ?) "
                + cursor_where
                + "ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
                tuple(params),
            )
        newest_first = [
            {
                **dict(row),
                "interrupted": bool(row["interrupted"]),
                "usage_estimated": bool(row["usage_estimated"]),
            }
            for row in rows[:limit]
        ]
        has_more = len(rows) > limit
        next_cursor = None
        if has_more and newest_first:
            oldest = newest_first[-1]
            next_cursor = encode_cursor(
                CursorPosition(str(oldest["created_at"]), str(oldest["id"]))
            )
        return CursorPage(
            items=list(reversed(newest_first)),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def list_memory_messages(
        self,
        user_id: str,
        agent_id: str,
        exclude_conversation_id: str | None = None,
        *,
        limit: int = 200,
        chars_per_message: int = 2_000,
    ) -> List[Dict[str, Any]]:
        """Recuerdos recientes en una consulta, con filas y texto acotados en SQL."""
        excluded = exclude_conversation_id or ""
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/chat:recent_context"),
                (chars_per_message, user_id, agent_id, excluded, limit),
            )
            return [dict(row) for row in rows]
