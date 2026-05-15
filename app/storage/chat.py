"""Chat history storage — SQLite/PostgreSQL conversations and messages."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import IS_PG, PH, close_db, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStorage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def _conn(self):
        return open_db(self._db_path)

    # ── Conversations ──────────────────────────────────────────────────────────

    def list_conversations(
        self, user_id: str, agent_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, user_id, agent_id, title, created_at, updated_at "
                f"FROM conversations WHERE user_id = {PH} AND agent_id = {PH} "
                f"ORDER BY updated_at DESC LIMIT {PH}",
                (user_id, agent_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    def new_conversation(
        self, user_id: str, agent_id: str, title: str = ""
    ) -> Dict[str, Any]:
        conv_id = uuid4().hex
        now = _now()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO conversations (id, user_id, agent_id, title, created_at, updated_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
                (conv_id, user_id, agent_id, title or "", now, now),
            )
            conn.commit()
        finally:
            close_db(conn)
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
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, user_id, agent_id, title, created_at, updated_at "
                f"FROM conversations WHERE id = {PH} AND user_id = {PH}",
                (conv_id, user_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            close_db(conn)

    def touch_conversation(self, conv_id: str, title: str = "") -> None:
        """Update updated_at; set title only if it was empty."""
        now = _now()
        conn = self._conn()
        try:
            cur = conn.cursor()
            if title:
                if IS_PG:
                    cur.execute(
                        f"UPDATE conversations "
                        f"SET updated_at = {PH}, title = CASE WHEN title = '' THEN {PH} ELSE title END "
                        f"WHERE id = {PH}",
                        (now, title, conv_id),
                    )
                else:
                    cur.execute(
                        f"UPDATE conversations "
                        f"SET updated_at = {PH}, title = CASE WHEN title = '' THEN {PH} ELSE title END "
                        f"WHERE id = {PH}",
                        (now, title, conv_id),
                    )
            else:
                cur.execute(
                    f"UPDATE conversations SET updated_at = {PH} WHERE id = {PH}",
                    (now, conv_id),
                )
            conn.commit()
        finally:
            close_db(conn)

    def delete_conversation(self, conv_id: str, user_id: str) -> bool:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM conversations WHERE id = {PH} AND user_id = {PH}",
                (conv_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    # ── Messages ───────────────────────────────────────────────────────────────

    def add_message(
        self, conv_id: str, role: str, content: str
    ) -> Dict[str, Any]:
        msg_id = uuid4().hex
        now = _now()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO messages (id, conversation_id, role, content, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH})",
                (msg_id, conv_id, role, content, now),
            )
            conn.commit()
        finally:
            close_db(conn)
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
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, role, content, created_at FROM messages "
                f"WHERE conversation_id = {PH} ORDER BY created_at ASC LIMIT {PH}",
                (conv_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)
