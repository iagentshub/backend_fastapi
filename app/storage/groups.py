"""GroupStorage — grupos dentro de workspaces para compartir recursos."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import IS_PG, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GroupStorage:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    # ── Grupos ─────────────────────────────────────────────────────────────────

    async def create(
        self, workspace_id: str, name: str, created_by: str
    ) -> Dict[str, Any]:
        group_id = uuid4().hex[:16]
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                row = await conn.fetchone(
                    "INSERT INTO workspace_groups "
                    "(id, workspace_id, name, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (workspace_id, name) DO NOTHING RETURNING id",
                    (group_id, workspace_id, name.strip(), created_by, now),
                )
            else:
                row = await conn.fetchone(
                    "INSERT OR IGNORE INTO workspace_groups "
                    "(id, workspace_id, name, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?) RETURNING id",
                    (group_id, workspace_id, name.strip(), created_by, now),
                )
            if row is None:
                raise ValueError(
                    f"Ya existe un grupo llamado '{name}' en este workspace"
                )
            await conn.commit()
        return {
            "id": group_id,
            "workspace_id": workspace_id,
            "name": name.strip(),
            "created_by": created_by,
            "created_at": now,
        }

    async def get(self, group_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM workspace_groups WHERE id = ?", (group_id,)
            )
            return dict(row) if row else None

    async def rename(self, group_id: str, name: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE workspace_groups SET name = ? WHERE id = ? RETURNING id",
                (name.strip(), group_id),
            )
            await conn.commit()
            return row is not None

    async def delete(self, group_id: str) -> bool:
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM workspace_group_members WHERE group_id = ?",
                    (group_id,),
                )
                await conn.execute(
                    "DELETE FROM resource_groups WHERE group_id = ?", (group_id,)
                )
                row = await conn.fetchone(
                    "DELETE FROM workspace_groups WHERE id = ? RETURNING id",
                    (group_id,),
                )
            return row is not None

    async def list_for_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT wg.*, COUNT(wgm.username) AS member_count "
                "FROM workspace_groups wg "
                "LEFT JOIN workspace_group_members wgm ON wg.id = wgm.group_id "
                "WHERE wg.workspace_id = ? "
                "GROUP BY wg.id ORDER BY wg.created_at ASC",
                (workspace_id,),
            )
            return [dict(r) for r in rows]

    # ── Miembros ───────────────────────────────────────────────────────────────

    async def list_members(self, group_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT wgm.*, u.display_name FROM workspace_group_members wgm "
                "LEFT JOIN users u ON u.username = wgm.username "
                "WHERE wgm.group_id = ? ORDER BY wgm.joined_at ASC",
                (group_id,),
            )
            return [dict(r) for r in rows]

    async def is_member(self, group_id: str, username: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT 1 FROM workspace_group_members "
                "WHERE group_id = ? AND username = ?",
                (group_id, username),
            )
            return row is not None

    async def add_member(
        self, group_id: str, workspace_id: str, username: str
    ) -> bool:
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                await conn.execute(
                    "INSERT INTO workspace_group_members "
                    "(group_id, workspace_id, username, joined_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (group_id, username) DO NOTHING",
                    (group_id, workspace_id, username, now),
                )
            else:
                await conn.execute(
                    "INSERT OR IGNORE INTO workspace_group_members "
                    "(group_id, workspace_id, username, joined_at) VALUES (?, ?, ?, ?)",
                    (group_id, workspace_id, username, now),
                )
            await conn.commit()
            return True

    async def remove_member(self, group_id: str, username: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM workspace_group_members "
                "WHERE group_id = ? AND username = ? RETURNING group_id",
                (group_id, username),
            )
            await conn.commit()
            return row is not None

    # ── Compartición de recursos ───────────────────────────────────────────────

    async def share_resource(
        self,
        resource_type: str,
        resource_id: str,
        group_id: str,
        shared_by: str,
    ) -> bool:
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                await conn.execute(
                    "INSERT INTO resource_groups "
                    "(resource_type, resource_id, group_id, shared_by, shared_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (resource_type, resource_id, group_id) DO UPDATE "
                    "SET shared_by = EXCLUDED.shared_by, shared_at = EXCLUDED.shared_at",
                    (resource_type, resource_id, group_id, shared_by, now),
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO resource_groups "
                    "(resource_type, resource_id, group_id, shared_by, shared_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (resource_type, resource_id, group_id, shared_by, now),
                )
            await conn.commit()
            return True

    async def unshare_resource(
        self, resource_type: str, resource_id: str, group_id: str
    ) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM resource_groups "
                "WHERE resource_type = ? AND resource_id = ? AND group_id = ? "
                "RETURNING group_id",
                (resource_type, resource_id, group_id),
            )
            await conn.commit()
            return row is not None

    async def get_resource_groups(
        self, resource_type: str, resource_id: str
    ) -> List[Dict[str, Any]]:
        """Grupos con los que está compartido un recurso (enriquecido con nombre del grupo)."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT rg.*, wg.name AS group_name, wg.workspace_id "
                "FROM resource_groups rg "
                "JOIN workspace_groups wg ON rg.group_id = wg.id "
                "WHERE rg.resource_type = ? AND rg.resource_id = ?",
                (resource_type, resource_id),
            )
            return [dict(r) for r in rows]

    async def get_group_resources(
        self, group_id: str, resource_type: str
    ) -> List[Dict[str, Any]]:
        """Recursos compartidos con un grupo."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM resource_groups "
                "WHERE group_id = ? AND resource_type = ? ORDER BY shared_at DESC",
                (group_id, resource_type),
            )
            return [dict(r) for r in rows]

    async def get_user_shared_resource_ids(
        self, username: str, resource_type: str, workspace_id: str
    ) -> List[str]:
        """IDs de recursos compartidos con grupos a los que pertenece el usuario dentro de un workspace."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT DISTINCT rg.resource_id FROM resource_groups rg "
                "JOIN workspace_group_members wgm ON rg.group_id = wgm.group_id "
                "JOIN workspace_groups wg ON rg.group_id = wg.id "
                "WHERE wgm.username = ? AND rg.resource_type = ? AND wg.workspace_id = ?",
                (username, resource_type, workspace_id),
            )
            return [r[0] for r in rows]
