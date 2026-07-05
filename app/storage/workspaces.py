"""WorkspaceStorage — gestión de workspaces de equipo.

Los workspaces personales son virtuales (workspace_id = username), sin entrada en BD.
Solo los workspaces de equipo tienen filas en las tablas workspaces / workspace_members.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import IS_PG, open_db
from app.utils import now_iso as _now

_VALID_ROLES: frozenset[str] = frozenset({"owner", "admin", "member"})


def _row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


class WorkspaceStorage:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    # ── Workspaces ─────────────────────────────────────────────────────────────

    async def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            )
            return _row(row)

    async def list_for_user(self, username: str) -> List[Dict[str, Any]]:
        """Devuelve todos los workspaces de equipo donde el usuario es miembro."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT w.*, wm.role FROM workspaces w "
                "JOIN workspace_members wm ON w.id = wm.workspace_id "
                "WHERE wm.username = ? ORDER BY w.created_at ASC",
                (username,),
            )
            return [dict(r) for r in rows]

    async def create(self, name: str, created_by: str) -> Dict[str, Any]:
        ws_id = uuid4().hex[:16]
        now = _now()
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO workspaces (id, name, created_by, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (ws_id, name.strip(), created_by, now),
                )
                await conn.execute(
                    "INSERT INTO workspace_members (workspace_id, username, role, joined_at) "
                    "VALUES (?, ?, ?, ?)",
                    (ws_id, created_by, "owner", now),
                )
        return {
            "id": ws_id,
            "name": name.strip(),
            "created_by": created_by,
            "created_at": now,
            "role": "owner",
        }

    async def update(self, workspace_id: str, name: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE workspaces SET name = ? WHERE id = ? RETURNING id",
                (name.strip(), workspace_id),
            )
            await conn.commit()
            return row is not None

    async def delete(self, workspace_id: str) -> bool:
        """Elimina el workspace y TODO su contenido: lo creado directamente en él y
        lo enlazado/forkeado hacia él (una vez copiado, vive ahí — no se toca el
        original en su owner real). No afecta a nada fuera del workspace."""
        async with open_db() as conn:
            async with conn.transaction():
                agent_ids = [
                    r[0] for r in await conn.fetchall(
                        "SELECT id FROM agents WHERE owner_id = ?", (workspace_id,)
                    )
                ]
                skill_ids = [
                    r[0] for r in await conn.fetchall(
                        "SELECT id FROM skills WHERE owner_id = ?", (workspace_id,)
                    )
                ]
                knowledge_ids = [
                    r[0] for r in await conn.fetchall(
                        "SELECT id FROM knowledge_items WHERE owner_id = ?", (workspace_id,)
                    )
                ]
                connection_ids = [
                    r[0] for r in await conn.fetchall(
                        "SELECT id FROM connections WHERE owner_id = ?", (workspace_id,)
                    )
                ]

                for resource_type, ids in (
                    ("agent", agent_ids),
                    ("skill", skill_ids),
                    ("knowledge", knowledge_ids),
                    ("connection", connection_ids),
                ):
                    for rid in ids:
                        await conn.execute(
                            "DELETE FROM resource_social WHERE resource_type = ? AND resource_id = ?",
                            (resource_type, rid),
                        )
                        await conn.execute(
                            "DELETE FROM resource_workspace_shares WHERE resource_type = ? AND resource_id = ?",
                            (resource_type, rid),
                        )

                await conn.execute("DELETE FROM agents WHERE owner_id = ?", (workspace_id,))
                await conn.execute("DELETE FROM skills WHERE owner_id = ?", (workspace_id,))
                await conn.execute("DELETE FROM knowledge_items WHERE owner_id = ?", (workspace_id,))
                await conn.execute("DELETE FROM knowledge_folders WHERE owner_id = ?", (workspace_id,))
                await conn.execute("DELETE FROM connections WHERE owner_id = ?", (workspace_id,))
                await conn.execute(
                    "DELETE FROM resource_workspace_shares WHERE workspace_id = ?", (workspace_id,)
                )
                await conn.execute(
                    "DELETE FROM workspace_invitations WHERE workspace_id = ?", (workspace_id,)
                )
                await conn.execute(
                    "DELETE FROM workspace_members WHERE workspace_id = ?",
                    (workspace_id,),
                )
                row = await conn.fetchone(
                    "DELETE FROM workspaces WHERE id = ? RETURNING id",
                    (workspace_id,),
                )
            return row is not None

    async def set_status(self, workspace_id: str, status: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE workspaces SET status = ? WHERE id = ? RETURNING id",
                (status, workspace_id),
            )
            await conn.commit()
            return row is not None

    async def is_active(self, workspace_id: str, username: str) -> bool:
        """True si el workspace está activo. Un workspace personal siempre lo está."""
        if workspace_id == username:
            return True
        ws = await self.get(workspace_id)
        return bool(ws) and ws.get("status", "active") == "active"

    async def owner_is_active(self, owner_id: str) -> bool:
        """True si owner_id es un espacio personal (no hay fila en workspaces) o un
        workspace de equipo activo. Usado para bloquear contenido compartido desde
        un workspace desactivado."""
        ws = await self.get(owner_id)
        if not ws:
            return True
        return ws.get("status", "active") == "active"

    async def transfer_ownership(self, workspace_id: str, new_owner: str) -> bool:
        """Transfer ownership to an existing member. Returns False if not a member."""
        async with open_db() as conn:
            member = await conn.fetchone(
                "SELECT 1 FROM workspace_members WHERE workspace_id = ? AND username = ?",
                (workspace_id, new_owner),
            )
            if not member:
                return False
            async with conn.transaction():
                await conn.execute(
                    "UPDATE workspaces SET created_by = ? WHERE id = ?",
                    (new_owner, workspace_id),
                )
                await conn.execute(
                    "UPDATE workspace_members SET role = ? WHERE workspace_id = ? AND username = ?",
                    ("owner", workspace_id, new_owner),
                )
            return True

    # ── Members ────────────────────────────────────────────────────────────────

    async def list_members(self, workspace_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT wm.username, wm.role, wm.joined_at, u.display_name, u.email "
                "FROM workspace_members wm "
                "LEFT JOIN users u ON u.username = wm.username "
                "WHERE wm.workspace_id = ? ORDER BY wm.joined_at ASC",
                (workspace_id,),
            )
            return [dict(r) for r in rows]

    async def get_member(self, workspace_id: str, username: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM workspace_members WHERE workspace_id = ? AND username = ?",
                (workspace_id, username),
            )
            return _row(row)

    async def is_member(self, workspace_id: str, username: str) -> bool:
        """True si el usuario pertenece a este workspace de equipo."""
        return await self.get_member(workspace_id, username) is not None

    async def add_member(
        self, workspace_id: str, username: str, role: str = "member"
    ) -> bool:
        if role not in _VALID_ROLES:
            return False
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                await conn.execute(
                    "INSERT INTO workspace_members (workspace_id, username, role, joined_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (workspace_id, username) DO UPDATE SET role = ?",
                    (workspace_id, username, role, now, role),
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO workspace_members "
                    "(workspace_id, username, role, joined_at) VALUES (?, ?, ?, ?)",
                    (workspace_id, username, role, now),
                )
            await conn.commit()
            return True

    async def remove_member(self, workspace_id: str, username: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM workspace_members "
                "WHERE workspace_id = ? AND username = ? RETURNING workspace_id",
                (workspace_id, username),
            )
            await conn.commit()
            return row is not None

    async def update_member_role(
        self, workspace_id: str, username: str, role: str
    ) -> bool:
        if role not in _VALID_ROLES:
            return False
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE workspace_members SET role = ? "
                "WHERE workspace_id = ? AND username = ? RETURNING workspace_id",
                (role, workspace_id, username),
            )
            await conn.commit()
            return row is not None

    # ── Authorization helpers ──────────────────────────────────────────────────

    async def can_access(self, workspace_id: str, username: str) -> bool:
        """True si el usuario puede usar este workspace.

        Un workspace personal (id == username) siempre es accesible por ese usuario.
        Un workspace de equipo requiere membresía.
        """
        if workspace_id == username:
            return True
        return await self.is_member(workspace_id, username)

    async def can_manage(self, workspace_id: str, username: str) -> bool:
        """True si el usuario puede modificar configuración del workspace (owner o admin)."""
        if workspace_id == username:
            return True
        member = await self.get_member(workspace_id, username)
        return member is not None and member.get("role") in ("owner", "admin")

    # ── Invitaciones ───────────────────────────────────────────────────────────

    async def invite_user(
        self, workspace_id: str, username: str, invited_by: str
    ) -> Optional[Dict[str, Any]]:
        inv_id = uuid4().hex[:16]
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                row = await conn.fetchone(
                    "INSERT INTO workspace_invitations "
                    "(id, workspace_id, invited_by, username, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?) "
                    "ON CONFLICT (workspace_id, username) DO NOTHING RETURNING id",
                    (inv_id, workspace_id, invited_by, username, now),
                )
            else:
                row = await conn.fetchone(
                    "INSERT OR IGNORE INTO workspace_invitations "
                    "(id, workspace_id, invited_by, username, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?) RETURNING id",
                    (inv_id, workspace_id, invited_by, username, now),
                )
            await conn.commit()
            if row is None:
                return None  # Already invited or already a member
            return {
                "id": inv_id,
                "workspace_id": workspace_id,
                "invited_by": invited_by,
                "username": username,
                "status": "pending",
                "created_at": now,
            }

    async def list_invitations(self, workspace_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM workspace_invitations "
                "WHERE workspace_id = ? AND status = 'pending' ORDER BY created_at DESC",
                (workspace_id,),
            )
            return [dict(r) for r in rows]

    async def list_my_invitations(self, username: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT wi.*, w.name AS workspace_name FROM workspace_invitations wi "
                "LEFT JOIN workspaces w ON w.id = wi.workspace_id "
                "WHERE wi.username = ? AND wi.status = 'pending' ORDER BY wi.created_at DESC",
                (username,),
            )
            return [dict(r) for r in rows]

    async def cancel_invitation(self, inv_id: str, workspace_id: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM workspace_invitations "
                "WHERE id = ? AND workspace_id = ? RETURNING id",
                (inv_id, workspace_id),
            )
            await conn.commit()
            return row is not None

    async def accept_invitation(self, inv_id: str, username: str) -> Optional[str]:
        """Acepta la invitación y añade al usuario como miembro. Devuelve workspace_id."""
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM workspace_invitations "
                "WHERE id = ? AND username = ? AND status = 'pending'",
                (inv_id, username),
            )
            if not row:
                return None
            workspace_id = dict(row)["workspace_id"]
            now = _now()
            async with conn.transaction():
                if IS_PG:
                    await conn.execute(
                        "INSERT INTO workspace_members "
                        "(workspace_id, username, role, joined_at) "
                        "VALUES (?, ?, 'member', ?) "
                        "ON CONFLICT (workspace_id, username) DO NOTHING",
                        (workspace_id, username, now),
                    )
                else:
                    await conn.execute(
                        "INSERT OR IGNORE INTO workspace_members "
                        "(workspace_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
                        (workspace_id, username, now),
                    )
                await conn.execute(
                    "DELETE FROM workspace_invitations WHERE id = ?", (inv_id,)
                )
            return workspace_id

    async def reject_invitation(self, inv_id: str, username: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM workspace_invitations "
                "WHERE id = ? AND username = ? RETURNING id",
                (inv_id, username),
            )
            await conn.commit()
            return row is not None
