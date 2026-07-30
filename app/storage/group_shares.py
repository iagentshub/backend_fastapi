"""GroupShareStorage — conceder acceso de un recurso a TODO un group.

No mueve ni copia el recurso: solo registra que ese group puede usarlo. El
dueño (owner_id) no cambia — sirve sobre todo para conexiones (credenciales),
donde duplicar el secreto sería un riesgo de seguridad.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from app.storage.db import IS_PG, open_db
from app.utils import now_iso as _now

if TYPE_CHECKING:
    from app.storage.groups import GroupStorage


class GroupShareStorage:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    async def share_with_group(
        self,
        resource_type: str,
        resource_id: str,
        group_id: str,
        shared_by: str,
    ) -> bool:
        """Concede acceso de uso a TODO el group, sin mover ni copiar el recurso."""
        now = _now()
        async with open_db() as conn:
            if IS_PG:
                await conn.execute(
                    "INSERT INTO resource_group_shares "
                    "(resource_type, resource_id, group_id, shared_by, shared_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (resource_type, resource_id, group_id) DO UPDATE "
                    "SET shared_by = EXCLUDED.shared_by, shared_at = EXCLUDED.shared_at",
                    (resource_type, resource_id, group_id, shared_by, now),
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO resource_group_shares "
                    "(resource_type, resource_id, group_id, shared_by, shared_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (resource_type, resource_id, group_id, shared_by, now),
                )
            await conn.commit()
            return True

    async def unshare_from_group(
        self, resource_type: str, resource_id: str, group_id: str
    ) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "DELETE FROM resource_group_shares "
                "WHERE resource_type = ? AND resource_id = ? AND group_id = ? "
                "RETURNING group_id",
                (resource_type, resource_id, group_id),
            )
            await conn.commit()
            return row is not None

    async def get_group_shared_resource_ids(
        self, group_id: str, resource_type: str
    ) -> List[str]:
        """IDs de recursos compartidos directamente con TODO el group."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT resource_id FROM resource_group_shares "
                "WHERE group_id = ? AND resource_type = ?",
                (group_id, resource_type),
            )
            return [r[0] for r in rows]

    async def is_accessible(
        self,
        groups: "GroupStorage",
        *,
        resource_type: str,
        resource_id: str,
        owner_id: Optional[str],
        requester: str,
        requester_group: Optional[str] = None,
    ) -> bool:
        """True si `requester` puede legítimamente usar el contenido de este
        recurso: es su propietario (personal o vía group activo), o está
        compartido con alguno de los grupos a los que pertenece.

        Pensado para puntos donde se RESUELVE contenido privado (chat,
        export, preview) a partir de un ID que el propio recurso referencia
        (p. ej. los skills/knowledge de un agente) — sin esto, cualquiera
        puede leer contenido ajeno con solo conocer el ID (ver ALTO-5/A1 en
        sharing.py, mismo problema sin corregir aquí).
        """
        if owner_id is not None and owner_id in (requester, requester_group):
            return True
        for group in await groups.list_for_user(requester):
            shared_ids = await self.get_group_shared_resource_ids(
                group["id"], resource_type
            )
            if resource_id in shared_ids:
                return True
        return False
