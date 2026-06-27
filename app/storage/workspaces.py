"""WorkspaceStorage — gestión de workspaces de equipo.

Los workspaces personales son virtuales (workspace_id = username), sin entrada en BD.
Solo los workspaces de equipo tienen filas en las tablas workspaces / workspace_members.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import PH, close_db, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


class WorkspaceStorage:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    # ── Workspaces ─────────────────────────────────────────────────────────────

    def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM workspaces WHERE id = {PH}", (workspace_id,))
            return _row(cur.fetchone())
        finally:
            close_db(conn)

    def list_for_user(self, username: str) -> List[Dict[str, Any]]:
        """Devuelve todos los workspaces de equipo donde el usuario es miembro."""
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT w.*, wm.role FROM workspaces w "
                f"JOIN workspace_members wm ON w.id = wm.workspace_id "
                f"WHERE wm.username = {PH} ORDER BY w.created_at ASC",
                (username,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    def create(self, name: str, created_by: str) -> Dict[str, Any]:
        ws_id = uuid4().hex[:16]
        now = _now()
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO workspaces (id, name, created_by, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH})",
                (ws_id, name.strip(), created_by, now),
            )
            cur.execute(
                f"INSERT INTO workspace_members (workspace_id, username, role, joined_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH})",
                (ws_id, created_by, "owner", now),
            )
            conn.commit()
        finally:
            close_db(conn)
        return {"id": ws_id, "name": name.strip(), "created_by": created_by, "created_at": now, "role": "owner"}

    def update(self, workspace_id: str, name: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE workspaces SET name = {PH} WHERE id = {PH}",
                (name.strip(), workspace_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    def delete(self, workspace_id: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM workspace_members WHERE workspace_id = {PH}", (workspace_id,))
            cur.execute(f"DELETE FROM workspaces WHERE id = {PH}", (workspace_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    def transfer_ownership(self, workspace_id: str, new_owner: str) -> bool:
        """Transfer ownership to an existing member. Returns False if not a member."""
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT 1 FROM workspace_members WHERE workspace_id = {PH} AND username = {PH}",
                (workspace_id, new_owner),
            )
            if not cur.fetchone():
                return False
            cur.execute(
                f"UPDATE workspaces SET created_by = {PH} WHERE id = {PH}",
                (new_owner, workspace_id),
            )
            cur.execute(
                f"UPDATE workspace_members SET role = {PH} WHERE workspace_id = {PH} AND username = {PH}",
                ("owner", workspace_id, new_owner),
            )
            conn.commit()
            return True
        finally:
            close_db(conn)

    # ── Members ────────────────────────────────────────────────────────────────

    def list_members(self, workspace_id: str) -> List[Dict[str, Any]]:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT wm.username, wm.role, wm.joined_at, u.display_name, u.email "
                f"FROM workspace_members wm "
                f"LEFT JOIN users u ON u.username = wm.username "
                f"WHERE wm.workspace_id = {PH} ORDER BY wm.joined_at ASC",
                (workspace_id,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    def get_member(self, workspace_id: str, username: str) -> Optional[Dict[str, Any]]:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM workspace_members WHERE workspace_id = {PH} AND username = {PH}",
                (workspace_id, username),
            )
            return _row(cur.fetchone())
        finally:
            close_db(conn)

    def is_member(self, workspace_id: str, username: str) -> bool:
        """True si el usuario pertenece a este workspace de equipo."""
        return self.get_member(workspace_id, username) is not None

    def add_member(self, workspace_id: str, username: str, role: str = "member") -> bool:
        if role not in ("owner", "admin", "member"):
            return False
        now = _now()
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            if PH == "%s":
                cur.execute(
                    "INSERT INTO workspace_members (workspace_id, username, role, joined_at) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (workspace_id, username) DO UPDATE SET role = %s",
                    (workspace_id, username, role, now, role),
                )
            else:
                cur.execute(
                    "INSERT OR REPLACE INTO workspace_members (workspace_id, username, role, joined_at) "
                    "VALUES (?, ?, ?, ?)",
                    (workspace_id, username, role, now),
                )
            conn.commit()
            return True
        finally:
            close_db(conn)

    def remove_member(self, workspace_id: str, username: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM workspace_members WHERE workspace_id = {PH} AND username = {PH}",
                (workspace_id, username),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    def update_member_role(self, workspace_id: str, username: str, role: str) -> bool:
        if role not in ("owner", "admin", "member"):
            return False
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE workspace_members SET role = {PH} WHERE workspace_id = {PH} AND username = {PH}",
                (role, workspace_id, username),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    # ── Authorization helpers ──────────────────────────────────────────────────

    def can_access(self, workspace_id: str, username: str) -> bool:
        """True si el usuario puede usar este workspace.

        Un workspace personal (id == username) siempre es accesible por ese usuario.
        Un workspace de equipo requiere membresía.
        """
        if workspace_id == username:
            return True
        return self.is_member(workspace_id, username)

    def can_manage(self, workspace_id: str, username: str) -> bool:
        """True si el usuario puede modificar configuración del workspace (owner o admin)."""
        if workspace_id == username:
            return True
        member = self.get_member(workspace_id, username)
        return member is not None and member.get("role") in ("owner", "admin")

    # ── Invitaciones ───────────────────────────────────────────────────────────

    def invite_user(self, workspace_id: str, username: str, invited_by: str) -> Optional[Dict[str, Any]]:
        inv_id = uuid4().hex[:16]
        now = _now()
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            if PH == "%s":
                cur.execute(
                    "INSERT INTO workspace_invitations (id, workspace_id, invited_by, username, status, created_at) "
                    "VALUES (%s, %s, %s, %s, 'pending', %s) "
                    "ON CONFLICT (workspace_id, username) DO NOTHING",
                    (inv_id, workspace_id, invited_by, username, now),
                )
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO workspace_invitations (id, workspace_id, invited_by, username, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?)",
                    (inv_id, workspace_id, invited_by, username, now),
                )
            conn.commit()
            if cur.rowcount == 0:
                return None  # Already invited or already a member
            return {"id": inv_id, "workspace_id": workspace_id, "invited_by": invited_by,
                    "username": username, "status": "pending", "created_at": now}
        finally:
            close_db(conn)

    def list_invitations(self, workspace_id: str) -> List[Dict[str, Any]]:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM workspace_invitations WHERE workspace_id = {PH} AND status = 'pending' "
                f"ORDER BY created_at DESC",
                (workspace_id,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    def list_my_invitations(self, username: str) -> List[Dict[str, Any]]:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT wi.*, w.name AS workspace_name FROM workspace_invitations wi "
                f"LEFT JOIN workspaces w ON w.id = wi.workspace_id "
                f"WHERE wi.username = {PH} AND wi.status = 'pending' ORDER BY wi.created_at DESC",
                (username,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            close_db(conn)

    def cancel_invitation(self, inv_id: str, workspace_id: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM workspace_invitations WHERE id = {PH} AND workspace_id = {PH}",
                (inv_id, workspace_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    def accept_invitation(self, inv_id: str, username: str) -> Optional[str]:
        """Acepta la invitación y añade al usuario como miembro. Devuelve workspace_id."""
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM workspace_invitations WHERE id = {PH} AND username = {PH} AND status = 'pending'",
                (inv_id, username),
            )
            row = cur.fetchone()
            if not row:
                return None
            workspace_id = dict(row)["workspace_id"]
            now = _now()
            if PH == "%s":
                cur.execute(
                    "INSERT INTO workspace_members (workspace_id, username, role, joined_at) "
                    "VALUES (%s, %s, 'member', %s) ON CONFLICT (workspace_id, username) DO NOTHING",
                    (workspace_id, username, now),
                )
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO workspace_members (workspace_id, username, role, joined_at) "
                    "VALUES (?, ?, 'member', ?)",
                    (workspace_id, username, now),
                )
            cur.execute(f"DELETE FROM workspace_invitations WHERE id = {PH}", (inv_id,))
            conn.commit()
            return workspace_id
        finally:
            close_db(conn)

    def reject_invitation(self, inv_id: str, username: str) -> bool:
        conn = open_db(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM workspace_invitations WHERE id = {PH} AND username = {PH}",
                (inv_id, username),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)
