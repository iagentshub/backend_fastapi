"""GroupStorage — gestión de groups de equipo.

Los groups personales son virtuales (group_id = username), sin entrada en BD.
Solo los groups de equipo tienen filas en las tablas groups / group_members.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from app.storage.db import IS_PG, open_db
from app.utils import now_iso as _now
from app.utils.generators import generate_id

_VALID_ROLES: frozenset[str] = frozenset({"owner", "admin", "member"})


def _row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    if "is_active" in data:
        active = bool(data["is_active"])
        data["is_active"] = active
        data["status"] = "active" if active else "disabled"
    return data


class GroupStorage:
    # ── Groups ─────────────────────────────────────────────────────────────

    async def get(self, group_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM groups WHERE id = ?", (group_id,)
            )
            return _row(row)

    async def list_for_user(self, username: str) -> List[Dict[str, Any]]:
        """Devuelve todos los groups de equipo donde el usuario es miembro."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT w.*, wm.role, u.username AS created_by_username FROM groups w "
                "JOIN group_members wm ON w.id = wm.group_id "
                "LEFT JOIN users u ON u.id = w.created_by "
                "WHERE wm.username = ? ORDER BY w.created_at ASC",
                (username,),
            )
            result: List[Dict[str, Any]] = []
            for row in rows:
                item = _row(row)
                if item is not None:
                    result.append(item)
            return result

    async def create(self, name: str, created_by: str) -> Dict[str, Any]:
        group_id = generate_id(16)
        now = _now()
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO groups (id, name, created_by, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (group_id, name.strip(), created_by, now),
                )
                await conn.execute(
                    "INSERT INTO group_members (group_id, username, role, joined_at) "
                    "VALUES (?, ?, ?, ?)",
                    (group_id, created_by, "owner", now),
                )
        return {
            "id": group_id,
            "name": name.strip(),
            "created_by": created_by,
            "created_at": now,
            "is_active": True,
            "status": "active",
            "role": "owner",
        }

    async def update(self, group_id: str, name: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE groups SET name = ? WHERE id = ? RETURNING id",
                (name.strip(), group_id),
            )
            await conn.commit()
            return row is not None

    async def delete(self, group_id: str) -> bool:
        """Elimina el group y TODO su contenido: lo creado directamente en él y
        lo enlazado hacia él (una vez copiado, vive ahí — no se toca el
        original en su owner real). No afecta a nada fuera del group."""
        async with open_db() as conn:
            async with conn.transaction():
                agent_ids = [
                    r[0]
                    for r in await conn.fetchall(
                        "SELECT id FROM agents WHERE owner_id = ?", (group_id,)
                    )
                ]
                skill_ids = [
                    r[0]
                    for r in await conn.fetchall(
                        "SELECT id FROM skills WHERE owner_id = ?", (group_id,)
                    )
                ]
                knowledge_ids = [
                    r[0]
                    for r in await conn.fetchall(
                        "SELECT id FROM knowledge_items WHERE owner_id = ?",
                        (group_id,),
                    )
                ]
                connection_ids = [
                    r[0]
                    for r in await conn.fetchall(
                        "SELECT id FROM connections WHERE owner_id = ?", (group_id,)
                    )
                ]
                workflow_ids = [
                    r[0]
                    for r in await conn.fetchall(
                        "SELECT id FROM agent_workflows WHERE owner_id = ?",
                        (group_id,),
                    )
                ]

                for resource_type, ids in (
                    ("agent", agent_ids),
                    ("skill", skill_ids),
                    ("knowledge", knowledge_ids),
                    ("connection", connection_ids),
                    ("workflow", workflow_ids),
                ):
                    for rid in ids:
                        await conn.execute(
                            "DELETE FROM resource_social WHERE resource_type = ? AND resource_id = ?",
                            (resource_type, rid),
                        )
                        await conn.execute(
                            "DELETE FROM resource_group_shares WHERE resource_type = ? AND resource_id = ?",
                            (resource_type, rid),
                        )

                await conn.execute(
                    "DELETE FROM agents WHERE owner_id = ?", (group_id,)
                )
                await conn.execute(
                    "DELETE FROM skills WHERE owner_id = ?", (group_id,)
                )
                await conn.execute(
                    "DELETE FROM knowledge_items WHERE owner_id = ?", (group_id,)
                )
                await conn.execute(
                    "DELETE FROM connections WHERE owner_id = ?", (group_id,)
                )
                await conn.execute(
                    "DELETE FROM agent_workflows WHERE owner_id = ?", (group_id,)
                )
                await conn.execute(
                    "DELETE FROM resource_group_shares WHERE group_id = ?",
                    (group_id,),
                )
                await conn.execute(
                    "DELETE FROM group_invitations WHERE group_id = ?",
                    (group_id,),
                )
                await conn.execute(
                    "DELETE FROM group_members WHERE group_id = ?",
                    (group_id,),
                )
                row = await conn.fetchone(
                    "DELETE FROM groups WHERE id = ? RETURNING id",
                    (group_id,),
                )
            return row is not None

    async def set_status(self, group_id: str, status: str) -> bool:
        active = 1 if status == "active" else 0
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE groups SET is_active = ? WHERE id = ? RETURNING id",
                (active, group_id),
            )
            await conn.commit()
            return row is not None

    async def is_active(self, group_id: str, username: str) -> bool:
        """True si el group está activo. Un group personal siempre lo está."""
        if group_id == username:
            return True
        group = await self.get(group_id)
        return bool(group) and bool(group.get("is_active", True))

    async def owner_is_active(self, owner_id: str) -> bool:
        """True si owner_id es un espacio personal (no hay fila en groups) o un
        group de equipo activo. Usado para bloquear contenido compartido desde
        un group desactivado."""
        group = await self.get(owner_id)
        if not group:
            return True
        return bool(group.get("is_active", True))

    async def transfer_ownership(self, group_id: str, new_owner: str) -> bool:
        """Transfer ownership to an existing member. Returns False if not a member."""
        async with open_db() as conn:
            member = await conn.fetchone(
                "SELECT 1 FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, new_owner),
            )
            if not member:
                return False
            async with conn.transaction():
                await conn.execute(
                    "UPDATE groups SET created_by = ? WHERE id = ?",
                    (new_owner, group_id),
                )
                await conn.execute(
                    "UPDATE group_members SET role = ? WHERE group_id = ? AND username = ?",
                    ("owner", group_id, new_owner),
                )
            return True

    # ── Members ────────────────────────────────────────────────────────────────

    async def list_members(self, group_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT u.username, wm.role, wm.permissions, wm.joined_at, u.display_name "
                "FROM group_members wm "
                "JOIN users u ON u.id = wm.username "
                "WHERE wm.group_id = ? ORDER BY wm.joined_at ASC",
                (group_id,),
            )
            result = []
            for row in rows:
                item = dict(row)
                try:
                    item["permissions"] = json.loads(item.get("permissions") or "{}")
                except (TypeError, ValueError):
                    item["permissions"] = {}
                result.append(item)
            return result

    async def get_member(
        self, group_id: str, username: str
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, username),
            )
            return _row(row)

    async def is_member(self, group_id: str, username: str) -> bool:
        """True si el usuario pertenece a este group de equipo."""
        return await self.get_member(group_id, username) is not None

    async def add_member(
        self, group_id: str, username: str, role: str = "member"
    ) -> bool:
        if role not in _VALID_ROLES:
            return False
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                await conn.execute(
                    "INSERT INTO group_members (group_id, username, role, joined_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (group_id, username) DO UPDATE SET role = ?",
                    (group_id, username, role, now, role),
                )
            else:
                await conn.execute(
                    "INSERT INTO group_members "
                    "(group_id, username, role, joined_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(group_id, username) "
                    "DO UPDATE SET role=excluded.role",
                    (group_id, username, role, now),
                )
            await conn.commit()
            return True

    async def remove_member(self, group_id: str, username: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM group_members "
                "WHERE group_id = ? AND username = ? RETURNING group_id",
                (group_id, username),
            )
            await conn.commit()
            return row is not None

    async def update_member_role(
        self, group_id: str, username: str, role: str
    ) -> bool:
        if role not in _VALID_ROLES:
            return False
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE group_members SET role = ? "
                "WHERE group_id = ? AND username = ? RETURNING group_id",
                (role, group_id, username),
            )
            await conn.commit()
            return row is not None

    async def update_member_permissions(
        self, group_id: str, username: str, permissions: Dict[str, Any]
    ) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE group_members SET permissions = ? "
                "WHERE group_id = ? AND username = ? RETURNING group_id",
                (json.dumps(permissions, ensure_ascii=False), group_id, username),
            )
            await conn.commit()
            return row is not None

    # ── Authorization helpers ──────────────────────────────────────────────────

    async def can_access(self, group_id: str, username: str) -> bool:
        """True si el usuario puede usar este group.

        Un group personal (id == username) siempre es accesible por ese usuario.
        Un group de equipo requiere membresía.
        """
        if group_id == username:
            return True
        return await self.is_member(group_id, username)

    async def can_manage(self, group_id: str, username: str) -> bool:
        """True si el usuario puede modificar configuración del group (owner o admin)."""
        if group_id == username:
            return True
        member = await self.get_member(group_id, username)
        return member is not None and member.get("role") in ("owner", "admin")

    @staticmethod
    def _resolve_permission(
        member: Optional[Dict[str, Any]],
        section: str,
        resource_id: str,
        action: str,
    ) -> bool:
        """Missing/empty config is allow-all; sin fila de miembro, nada."""
        if not member:
            return False
        if member.get("role") in ("owner", "admin"):
            return True
        try:
            permissions = json.loads(member.get("permissions") or "{}")
        except (TypeError, ValueError):
            permissions = {}
        config = permissions.get(section) or {}
        item = (config.get("items") or {}).get(resource_id)
        if isinstance(item, dict) and action in item:
            return bool(item[action])
        return bool(config.get("default", True))

    async def has_resource_permission(
        self,
        group_id: str,
        username: str,
        section: str,
        resource_id: str,
        action: str,
    ) -> bool:
        """Resolve granular member permissions; missing/empty config is allow-all."""
        if group_id == username:
            return True
        member = await self.get_member(group_id, username)
        return self._resolve_permission(member, section, resource_id, action)

    async def permission_checker(
        self, group_id: str, username: str
    ) -> "Callable[[str, str, str], bool]":
        """Predicado síncrono con **una sola** consulta a ``group_members``.

        Filtrar N recursos con ``has_resource_permission`` costaba N consultas
        que devolvían siempre la misma fila: el JSON de permisos del miembro es
        uno solo, no uno por recurso. Se resuelve el miembro aquí y las N
        comprobaciones pasan a ser en memoria.

        Conserva los tres atajos del original —group personal, miembro
        inexistente y rol owner/admin—, incluido el ``False`` cuando no hay fila
        de miembro: convertirlo en ``True`` abriría los recursos del group a
        quien ya no pertenece a él.
        """
        if group_id == username:
            return lambda section, resource_id, action: True
        member = await self.get_member(group_id, username)
        if not member:
            return lambda section, resource_id, action: False
        return lambda section, resource_id, action: self._resolve_permission(
            member, section, resource_id, action
        )

    # ── Invitaciones ───────────────────────────────────────────────────────────

    async def invite_user(
        self, group_id: str, username: str, invited_by: str
    ) -> Optional[Dict[str, Any]]:
        inv_id = generate_id(16)
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                row = await conn.fetchone(
                    "INSERT INTO group_invitations "
                    "(id, group_id, invited_by, username, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?) "
                    "ON CONFLICT (group_id, username) DO NOTHING RETURNING id",
                    (inv_id, group_id, invited_by, username, now),
                )
            else:
                row = await conn.fetchone(
                    "INSERT OR IGNORE INTO group_invitations "
                    "(id, group_id, invited_by, username, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?) RETURNING id",
                    (inv_id, group_id, invited_by, username, now),
                )
            await conn.commit()
            if row is None:
                return None  # Already invited or already a member
            return {
                "id": inv_id,
                "group_id": group_id,
                "invited_by": invited_by,
                "username": username,
                "status": "pending",
                "created_at": now,
            }

    async def list_invitations(self, group_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT wi.id, wi.group_id, wi.invited_by, u.username, wi.status, wi.created_at "
                "FROM group_invitations wi JOIN users u ON u.id = wi.username "
                "WHERE wi.group_id = ? AND wi.status = 'pending' ORDER BY wi.created_at DESC",
                (group_id,),
            )
            return [dict(r) for r in rows]

    async def list_my_invitations(self, username: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT wi.id, wi.group_id, wi.invited_by, u.username, wi.status, "
                "wi.created_at, w.name AS group_name FROM group_invitations wi "
                "LEFT JOIN groups w ON w.id = wi.group_id "
                "JOIN users u ON u.id = wi.username "
                "WHERE wi.username = ? AND wi.status = 'pending' ORDER BY wi.created_at DESC",
                (username,),
            )
            return [dict(r) for r in rows]

    async def cancel_invitation(self, inv_id: str, group_id: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM group_invitations "
                "WHERE id = ? AND group_id = ? RETURNING id",
                (inv_id, group_id),
            )
            await conn.commit()
            return row is not None

    async def accept_invitation(self, inv_id: str, username: str) -> Optional[str]:
        """Acepta la invitación y añade al usuario como miembro. Devuelve group_id."""
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM group_invitations "
                "WHERE id = ? AND username = ? AND status = 'pending'",
                (inv_id, username),
            )
            if not row:
                return None
            group_id = dict(row)["group_id"]
            now = _now()
            async with conn.transaction():
                if IS_PG:
                    await conn.execute(
                        "INSERT INTO group_members "
                        "(group_id, username, role, joined_at) "
                        "VALUES (?, ?, 'member', ?) "
                        "ON CONFLICT (group_id, username) DO NOTHING",
                        (group_id, username, now),
                    )
                else:
                    await conn.execute(
                        "INSERT OR IGNORE INTO group_members "
                        "(group_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
                        (group_id, username, now),
                    )
                await conn.execute(
                    "DELETE FROM group_invitations WHERE id = ?", (inv_id,)
                )
            return group_id

    async def reject_invitation(self, inv_id: str, username: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM group_invitations "
                "WHERE id = ? AND username = ? RETURNING id",
                (inv_id, username),
            )
            await conn.commit()
            return row is not None
