"""WorkspaceShareStorage — conceder acceso de un recurso a TODO un workspace.

No mueve ni copia el recurso: solo registra que ese workspace puede usarlo. El
dueño (owner_id) no cambia — sirve sobre todo para conexiones (credenciales),
donde duplicar el secreto sería un riesgo de seguridad.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from app.storage.db import IS_PG, open_db
from app.utils import now_iso as _now


class WorkspaceShareStorage:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    async def share_with_workspace(
        self,
        resource_type: str,
        resource_id: str,
        workspace_id: str,
        shared_by: str,
    ) -> bool:
        """Concede acceso de uso a TODO el workspace, sin mover ni copiar el recurso."""
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                await conn.execute(
                    "INSERT INTO resource_workspace_shares "
                    "(resource_type, resource_id, workspace_id, shared_by, shared_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (resource_type, resource_id, workspace_id) DO UPDATE "
                    "SET shared_by = EXCLUDED.shared_by, shared_at = EXCLUDED.shared_at",
                    (resource_type, resource_id, workspace_id, shared_by, now),
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO resource_workspace_shares "
                    "(resource_type, resource_id, workspace_id, shared_by, shared_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (resource_type, resource_id, workspace_id, shared_by, now),
                )
            await conn.commit()
            return True

    async def unshare_from_workspace(
        self, resource_type: str, resource_id: str, workspace_id: str
    ) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM resource_workspace_shares "
                "WHERE resource_type = ? AND resource_id = ? AND workspace_id = ? "
                "RETURNING workspace_id",
                (resource_type, resource_id, workspace_id),
            )
            await conn.commit()
            return row is not None

    async def get_workspace_shared_resource_ids(
        self, workspace_id: str, resource_type: str
    ) -> List[str]:
        """IDs de recursos compartidos directamente con TODO el workspace."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT resource_id FROM resource_workspace_shares "
                "WHERE workspace_id = ? AND resource_type = ?",
                (workspace_id, resource_type),
            )
            return [r[0] for r in rows]
