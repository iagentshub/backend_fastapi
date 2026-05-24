"""TeamStorage — gestión de equipos, miembros e invitaciones."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamStorage:
    def __init__(self, db_path: Path) -> None:
        from app.storage.db import IS_PG, PH, close_db, open_db
        self._db_path = Path(db_path)
        self._IS_PG = IS_PG
        self._PH = PH
        self._open_db = open_db
        self._close_db = close_db

    def _conn(self):
        return self._open_db(self._db_path)

    # ── Equipos ────────────────────────────────────────────────────────────────

    def create_team(self, name: str, created_by: str) -> Dict[str, Any]:
        import sqlite3 as _sqlite3
        team_id = str(uuid4())
        now = _now()
        conn = self._conn()
        try:
            conn.execute(
                f"INSERT INTO teams (id, name, created_by, created_at) "
                f"VALUES ({self._PH},{self._PH},{self._PH},{self._PH})",
                (team_id, name, created_by, now),
            )
            conn.execute(
                f"INSERT INTO team_members (team_id, username, is_manager, permissions, joined_at) "
                f"VALUES ({self._PH},{self._PH},1,'{{}}'::text,{self._PH})"
                if self._IS_PG else
                f"INSERT INTO team_members (team_id, username, is_manager, permissions, joined_at) "
                f"VALUES ({self._PH},{self._PH},1,'{{}}',{self._PH})",
                (team_id, created_by, now),
            )
            conn.commit()
        except _sqlite3.IntegrityError:
            raise ValueError(f"Ya tienes un equipo llamado «{name}»")
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ValueError(f"Ya tienes un equipo llamado «{name}»")
            raise
        finally:
            if self._IS_PG:
                self._close_db(conn)
        return {"id": team_id, "name": name, "created_by": created_by, "created_at": now}

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                f"SELECT * FROM teams WHERE id={self._PH}", (team_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def rename_team(self, team_id: str, name: str) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE teams SET name={self._PH} WHERE id={self._PH}", (name, team_id)
            )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def delete_team(self, team_id: str) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                f"DELETE FROM team_invitations WHERE team_id={self._PH}", (team_id,)
            )
            conn.execute(
                f"DELETE FROM team_members WHERE team_id={self._PH}", (team_id,)
            )
            conn.execute(
                f"DELETE FROM resource_teams WHERE team_id={self._PH}", (team_id,)
            )
            conn.execute(
                f"DELETE FROM teams WHERE id={self._PH}", (team_id,)
            )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def list_teams_for_user(self, username: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT t.*, tm.is_manager, tm.permissions, tm.joined_at as member_since "
                f"FROM teams t "
                f"JOIN team_members tm ON t.id=tm.team_id "
                f"WHERE tm.username={self._PH} "
                f"ORDER BY t.created_at DESC",
                (username,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    # ── Miembros ───────────────────────────────────────────────────────────────

    def add_member(
        self,
        team_id: str,
        username: str,
        is_manager: bool = False,
        permissions: Optional[Dict] = None,
    ) -> bool:
        now = _now()
        perms = json.dumps(permissions or {})
        conn = self._conn()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO team_members "
                f"(team_id, username, is_manager, permissions, joined_at) "
                f"VALUES ({self._PH},{self._PH},{self._PH},{self._PH},{self._PH})"
                if not self._IS_PG else
                f"INSERT INTO team_members "
                f"(team_id, username, is_manager, permissions, joined_at) "
                f"VALUES ({self._PH},{self._PH},{self._PH},{self._PH},{self._PH}) "
                f"ON CONFLICT (team_id, username) DO UPDATE "
                f"SET is_manager=EXCLUDED.is_manager, permissions=EXCLUDED.permissions",
                (team_id, username, 1 if is_manager else 0, perms, now),
            )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def remove_member(self, team_id: str, username: str) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                f"DELETE FROM team_members WHERE team_id={self._PH} AND username={self._PH}",
                (team_id, username),
            )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def update_member_permissions(
        self, team_id: str, username: str, permissions: Dict
    ) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE team_members SET permissions={self._PH} "
                f"WHERE team_id={self._PH} AND username={self._PH}",
                (json.dumps(permissions), team_id, username),
            )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def update_member_role(self, team_id: str, username: str, is_manager: bool) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE team_members SET is_manager={self._PH} "
                f"WHERE team_id={self._PH} AND username={self._PH}",
                (1 if is_manager else 0, team_id, username),
            )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def list_members(self, team_id: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT tm.*, u.email, u.display_name "
                f"FROM team_members tm "
                f"LEFT JOIN users u ON tm.username=u.username "
                f"WHERE tm.team_id={self._PH} "
                f"ORDER BY tm.is_manager DESC, tm.joined_at ASC",
                (team_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["permissions"] = json.loads(d.get("permissions") or "{}")
                except Exception:
                    d["permissions"] = {}
                result.append(d)
            return result
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def get_member(self, team_id: str, username: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                f"SELECT * FROM team_members WHERE team_id={self._PH} AND username={self._PH}",
                (team_id, username),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["permissions"] = json.loads(d.get("permissions") or "{}")
            except Exception:
                d["permissions"] = {}
            return d
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def is_manager(self, team_id: str, username: str) -> bool:
        member = self.get_member(team_id, username)
        return bool(member and member.get("is_manager"))

    def list_managed_teams(self, username: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT t.* FROM teams t "
                f"JOIN team_members tm ON t.id=tm.team_id "
                f"WHERE tm.username={self._PH} AND tm.is_manager=1",
                (username,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    # ── Invitaciones ───────────────────────────────────────────────────────────

    def create_invitation(
        self, team_id: str, email: str, invited_by: str, expire_hours: int = 48
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=expire_hours)
        ).isoformat()
        conn = self._conn()
        try:
            conn.execute(
                f"INSERT INTO team_invitations "
                f"(id, team_id, invited_email, invited_by, status, expires_at, created_at) "
                f"VALUES ({self._PH},{self._PH},{self._PH},{self._PH},'pending',{self._PH},{self._PH})",
                (token, team_id, email.lower(), invited_by, expires_at, now),
            )
            conn.commit()
        finally:
            if self._IS_PG:
                self._close_db(conn)
        return token

    def get_invitation(self, token: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                f"SELECT ti.*, t.name as team_name FROM team_invitations ti "
                f"JOIN teams t ON ti.team_id=t.id "
                f"WHERE ti.id={self._PH}",
                (token,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def list_invitations(self, team_id: str) -> List[Dict[str, Any]]:
        """Pending invitations for a team."""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM team_invitations "
                f"WHERE team_id={self._PH} AND status='pending' "
                f"ORDER BY created_at DESC",
                (team_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def list_invitations_for_email(self, email: str) -> List[Dict[str, Any]]:
        """Pending invitations for a user email."""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT ti.*, t.name as team_name, t.created_by as team_owner "
                f"FROM team_invitations ti "
                f"JOIN teams t ON ti.team_id=t.id "
                f"WHERE ti.invited_email={self._PH} AND ti.status='pending' "
                f"ORDER BY ti.created_at DESC",
                (email.lower(),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def list_invitations_for_email_all(self, email: str) -> List[Dict[str, Any]]:
        """All invitations (any status) for a user email — for history view."""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT ti.*, t.name as team_name, t.created_by as team_owner "
                f"FROM team_invitations ti "
                f"JOIN teams t ON ti.team_id=t.id "
                f"WHERE ti.invited_email={self._PH} "
                f"ORDER BY ti.created_at DESC",
                (email.lower(),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def list_invitations_sent_by(self, username: str) -> List[Dict[str, Any]]:
        """All invitations sent by a user across all teams they manage."""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT ti.*, t.name as team_name "
                f"FROM team_invitations ti "
                f"JOIN teams t ON ti.team_id=t.id "
                f"WHERE ti.invited_by={self._PH} "
                f"ORDER BY ti.created_at DESC",
                (username,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def consume_invitation(self, token: str, accept: bool) -> Optional[Dict[str, Any]]:
        """Accept or reject an invitation. Returns invitation dict if found and pending."""
        conn = self._conn()
        try:
            row = conn.execute(
                f"SELECT * FROM team_invitations WHERE id={self._PH} AND status='pending'",
                (token,),
            ).fetchone()
            if not row:
                return None
            inv = dict(row)
            # Check expiry
            expires_at = datetime.fromisoformat(inv["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                conn.execute(
                    f"UPDATE team_invitations SET status='expired' WHERE id={self._PH}",
                    (token,),
                )
                conn.commit()
                return None
            new_status = "accepted" if accept else "rejected"
            conn.execute(
                f"UPDATE team_invitations SET status={self._PH} WHERE id={self._PH}",
                (new_status, token),
            )
            conn.commit()
            inv["status"] = new_status
            return inv
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def cancel_invitation(self, token: str) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                f"DELETE FROM team_invitations WHERE id={self._PH}", (token,)
            )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    # ── Resource sharing ───────────────────────────────────────────────────────

    def share_resource(
        self, resource_type: str, resource_id: str, team_id: str, shared_by: str
    ) -> bool:
        now = _now()
        conn = self._conn()
        try:
            if self._IS_PG:
                conn.execute(
                    f"INSERT INTO resource_teams (resource_type, resource_id, team_id, shared_by, shared_at) "
                    f"VALUES ({self._PH},{self._PH},{self._PH},{self._PH},{self._PH}) "
                    f"ON CONFLICT (resource_type, resource_id, team_id) DO UPDATE SET shared_by=EXCLUDED.shared_by, shared_at=EXCLUDED.shared_at",
                    (resource_type, resource_id, team_id, shared_by, now),
                )
            else:
                conn.execute(
                    f"INSERT OR REPLACE INTO resource_teams "
                    f"(resource_type, resource_id, team_id, shared_by, shared_at) "
                    f"VALUES ({self._PH},{self._PH},{self._PH},{self._PH},{self._PH})",
                    (resource_type, resource_id, team_id, shared_by, now),
                )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def unshare_resource(
        self, resource_type: str, resource_id: str, team_id: str
    ) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                f"DELETE FROM resource_teams "
                f"WHERE resource_type={self._PH} AND resource_id={self._PH} AND team_id={self._PH}",
                (resource_type, resource_id, team_id),
            )
            conn.commit()
            return True
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def get_resource_teams(
        self, resource_type: str, resource_id: str
    ) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT rt.*, t.name as team_name FROM resource_teams rt "
                f"JOIN teams t ON rt.team_id=t.id "
                f"WHERE rt.resource_type={self._PH} AND rt.resource_id={self._PH}",
                (resource_type, resource_id),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def get_user_shared_resource_ids(
        self, username: str, resource_type: str
    ) -> List[str]:
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT DISTINCT rt.resource_id FROM resource_teams rt "
                f"JOIN team_members tm ON rt.team_id=tm.team_id "
                f"WHERE tm.username={self._PH} AND rt.resource_type={self._PH}",
                (username, resource_type),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    def get_team_shared_resources(
        self, team_id: str, resource_type: str
    ) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM resource_teams "
                f"WHERE team_id={self._PH} AND resource_type={self._PH} "
                f"ORDER BY shared_at DESC",
                (team_id, resource_type),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._IS_PG:
                self._close_db(conn)

    # ── Enforcement ────────────────────────────────────────────────────────────

    def check_resource_permission(
        self,
        team_id: str,
        username: str,
        resource_type: str,
        resource_id: str,
        perm: str,
    ) -> bool:
        """Check if username has `perm` on `resource_id` within `team_id`."""
        member = self.get_member(team_id, username)
        if not member:
            return False
        perms = member.get("permissions") or {}
        section = perms.get(resource_type, {})
        default = section.get("default", "deny")
        items = section.get("items", {})
        if resource_id in items:
            return bool(items[resource_id].get(perm, False))
        return default == "allow"

    def get_allowed_resource_ids(
        self, username: str, resource_type: str, perm: str
    ) -> Optional[List[str]]:
        """Return list of allowed resource IDs across all teams, or None if not a team member."""
        teams = self.list_teams_for_user(username)
        if not teams:
            return None

        allowed: set[str] = set()
        for t in teams:
            member = self.get_member(t["id"], username)
            if not member:
                continue
            perms = member.get("permissions") or {}
            section = perms.get(resource_type, {})
            default = section.get("default", "deny")
            items = section.get("items", {})
            if default == "allow":
                # Allowed everything except items explicitly set to False
                for rid, rperms in items.items():
                    if rperms.get(perm, True):
                        allowed.add(rid)
                # Signal that we can't enumerate (would need full resource list)
                # Return None meaning "all allowed minus explicit denies" — caller handles
                return None
            else:
                for rid, rperms in items.items():
                    if rperms.get(perm, False):
                        allowed.add(rid)
        return list(allowed)
