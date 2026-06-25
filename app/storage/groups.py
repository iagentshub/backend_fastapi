"""GroupStorage — grupos dentro de workspaces para compartir recursos."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import PH, close_db, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GroupStorage:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    # ── Grupos ─────────────────────────────────────────────────────────────────

    def create(self, workspace_id: str, name: str, created_by: str) -> Dict[str, Any]:
        group_id = uuid4().hex[:16]
        now = _now()
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            if PH == "%s":
                cur.execute(
                    "INSERT INTO workspace_groups (id, workspace_id, name, created_by, created_at) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (workspace_id, name) DO NOTHING",
                    (group_id, workspace_id, name.strip(), created_by, now),
                )
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO workspace_groups (id, workspace_id, name, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (group_id, workspace_id, name.strip(), created_by, now),
                )
            if cur.rowcount == 0:
                raise ValueError(f"Ya existe un grupo llamado '{name}' en este workspace")
            conn.commit()
        finally:
            close_db(conn)
        return {"id": group_id, "workspace_id": workspace_id, "name": name.strip(),
                "created_by": created_by, "created_at": now}

    def get(self, group_id: str) -> Optional[Dict[str, Any]]:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM workspace_groups WHERE id = {PH}", (group_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            close_db(conn)

    def rename(self, group_id: str, name: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE workspace_groups SET name = {PH} WHERE id = {PH}",
                (name.strip(), group_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    def delete(self, group_id: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM workspace_group_members WHERE group_id = {PH}", (group_id,))
            cur.execute(f"DELETE FROM resource_groups WHERE group_id = {PH}", (group_id,))
            cur.execute(f"DELETE FROM workspace_groups WHERE id = {PH}", (group_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    def list_for_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT wg.*, COUNT(wgm.username) AS member_count "
                f"FROM workspace_groups wg "
                f"LEFT JOIN workspace_group_members wgm ON wg.id = wgm.group_id "
                f"WHERE wg.workspace_id = {PH} "
                f"GROUP BY wg.id ORDER BY wg.created_at ASC",
                (workspace_id,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    # ── Miembros ───────────────────────────────────────────────────────────────

    def list_members(self, group_id: str) -> List[Dict[str, Any]]:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT wgm.*, u.display_name FROM workspace_group_members wgm "
                f"LEFT JOIN users u ON u.username = wgm.username "
                f"WHERE wgm.group_id = {PH} ORDER BY wgm.joined_at ASC",
                (group_id,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    def is_member(self, group_id: str, username: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT 1 FROM workspace_group_members WHERE group_id = {PH} AND username = {PH}",
                (group_id, username),
            )
            return cur.fetchone() is not None
        finally:
            close_db(conn)

    def add_member(self, group_id: str, workspace_id: str, username: str) -> bool:
        now = _now()
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            if PH == "%s":
                cur.execute(
                    "INSERT INTO workspace_group_members (group_id, workspace_id, username, joined_at) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (group_id, username) DO NOTHING",
                    (group_id, workspace_id, username, now),
                )
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO workspace_group_members (group_id, workspace_id, username, joined_at) "
                    "VALUES (?, ?, ?, ?)",
                    (group_id, workspace_id, username, now),
                )
            conn.commit()
            return True
        finally:
            close_db(conn)

    def remove_member(self, group_id: str, username: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM workspace_group_members WHERE group_id = {PH} AND username = {PH}",
                (group_id, username),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    # ── Compartición de recursos ───────────────────────────────────────────────

    def share_resource(
        self, resource_type: str, resource_id: str, group_id: str, shared_by: str
    ) -> bool:
        now = _now()
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            if PH == "%s":
                cur.execute(
                    "INSERT INTO resource_groups (resource_type, resource_id, group_id, shared_by, shared_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (resource_type, resource_id, group_id) DO UPDATE "
                    "SET shared_by = EXCLUDED.shared_by, shared_at = EXCLUDED.shared_at",
                    (resource_type, resource_id, group_id, shared_by, now),
                )
            else:
                cur.execute(
                    "INSERT OR REPLACE INTO resource_groups "
                    "(resource_type, resource_id, group_id, shared_by, shared_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (resource_type, resource_id, group_id, shared_by, now),
                )
            conn.commit()
            return True
        finally:
            close_db(conn)

    def unshare_resource(
        self, resource_type: str, resource_id: str, group_id: str
    ) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM resource_groups "
                f"WHERE resource_type = {PH} AND resource_id = {PH} AND group_id = {PH}",
                (resource_type, resource_id, group_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    def get_resource_groups(
        self, resource_type: str, resource_id: str
    ) -> List[Dict[str, Any]]:
        """Grupos con los que está compartido un recurso (enriquecido con nombre del grupo)."""
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT rg.*, wg.name AS group_name, wg.workspace_id "
                f"FROM resource_groups rg "
                f"JOIN workspace_groups wg ON rg.group_id = wg.id "
                f"WHERE rg.resource_type = {PH} AND rg.resource_id = {PH}",
                (resource_type, resource_id),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    def get_group_resources(
        self, group_id: str, resource_type: str
    ) -> List[Dict[str, Any]]:
        """Recursos compartidos con un grupo."""
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM resource_groups "
                f"WHERE group_id = {PH} AND resource_type = {PH} ORDER BY shared_at DESC",
                (group_id, resource_type),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    def get_user_shared_resource_ids(
        self, username: str, resource_type: str, workspace_id: str
    ) -> List[str]:
        """IDs de recursos compartidos con grupos a los que pertenece el usuario dentro de un workspace."""
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT DISTINCT rg.resource_id FROM resource_groups rg "
                f"JOIN workspace_group_members wgm ON rg.group_id = wgm.group_id "
                f"JOIN workspace_groups wg ON rg.group_id = wg.id "
                f"WHERE wgm.username = {PH} AND rg.resource_type = {PH} AND wg.workspace_id = {PH}",
                (username, resource_type, workspace_id),
            )
            return [r[0] for r in cur.fetchall()]
        finally:
            close_db(conn)
