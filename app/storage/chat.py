"""Chat history storage — SQLite conversations and messages."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStorage:
    def __init__(self, db_path: Path) -> None:
        from app.storage.db import open_db
        self._db = open_db(Path(db_path))

    # ── Conversations ──────────────────────────────────────────────────────────

    def list_conversations(
        self, user_id: str, agent_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, user_id, agent_id, title, created_at, updated_at "
            "FROM conversations WHERE user_id = ? AND agent_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, agent_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def new_conversation(
        self, user_id: str, agent_id: str, title: str = ""
    ) -> Dict[str, Any]:
        conv_id = uuid4().hex
        now = _now()
        self._db.execute(
            "INSERT INTO conversations (id, user_id, agent_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, user_id, agent_id, title or "", now, now),
        )
        self._db.commit()
        return {
            "id": conv_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }

    def get_conversation(
        self, conv_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT id, user_id, agent_id, title, created_at, updated_at "
            "FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def touch_conversation(self, conv_id: str, title: str = "") -> None:
        """Update updated_at; set title only if it was empty."""
        now = _now()
        if title:
            self._db.execute(
                "UPDATE conversations "
                "SET updated_at = ?, title = CASE WHEN title = '' THEN ? ELSE title END "
                "WHERE id = ?",
                (now, title, conv_id),
            )
        else:
            self._db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id),
            )
        self._db.commit()

    def delete_conversation(self, conv_id: str, user_id: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    # ── Messages ───────────────────────────────────────────────────────────────

    def add_message(
        self, conv_id: str, role: str, content: str
    ) -> Dict[str, Any]:
        msg_id = uuid4().hex
        now = _now()
        self._db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (msg_id, conv_id, role, content, now),
        )
        self._db.commit()
        return {
            "id": msg_id,
            "conversation_id": conv_id,
            "role": role,
            "content": content,
            "created_at": now,
        }

    def get_messages(
        self, conv_id: str, user_id: str, limit: int = 200
    ) -> List[Dict[str, Any]]:
        if not self.get_conversation(conv_id, user_id):
            return []
        rows = self._db.execute(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
            (conv_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
