"""GroupShareStorage — conceder acceso de un recurso a TODO un group.

No mueve ni copia el recurso: solo registra que ese group puede usarlo. El
dueño (owner_id) no cambia — sirve sobre todo para conexiones (credenciales),
donde duplicar el secreto sería un riesgo de seguridad.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from app.sql import sql
from app.storage.db import open_db
from app.utils import now_iso as _now

if TYPE_CHECKING:
    from app.storage.groups import GroupStorage


class GroupShareStorage:
    async def share_with_group(
        self,
        resource_type: str,
        resource_id: str,
        group_id: str,
        shared_by: str,
        via_cascade: bool = False,
    ) -> bool:
        """Concede acceso de uso a TODO el group, sin mover ni copiar el recurso.

        ``via_cascade`` marca lo que llega arrastrado por un agente o una
        orquestación, para poder retirarlo cuando se retire quien lo trajo. Una
        compartición hecha a mano gana siempre: si el recurso ya venía de una
        cascada y el usuario lo comparte explícitamente, deja de ser
        dependencia y sobrevive a la marcha del agente. Al revés no —una
        cascada no degrada a dependencia lo que ya era explícito.
        """
        now = _now()
        # El motor decide si conserva la marca previa; los dos casos se
        # resuelven en el mismo UPSERT para no leer antes de escribir.
        conflicto = (
            "ON CONFLICT (resource_type, resource_id, group_id) DO UPDATE SET "
            "shared_by = EXCLUDED.shared_by, shared_at = EXCLUDED.shared_at, "
            "via_cascade = CASE WHEN EXCLUDED.via_cascade = 0 THEN 0 "
            "ELSE resource_group_shares.via_cascade END"
        )
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO resource_group_shares "
                "(resource_type, resource_id, group_id, shared_by, shared_at, "
                "via_cascade) VALUES (?, ?, ?, ?, ?, ?) " + conflicto,
                (
                    resource_type,
                    resource_id,
                    group_id,
                    shared_by,
                    now,
                    1 if via_cascade else 0,
                ),
            )
            await conn.commit()
            return True

    async def cascaded_resources(self, group_id: str) -> List[tuple[str, str]]:
        """Pares (tipo, id) que el grupo solo ve porque los arrastró otro recurso."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/group_shares:cascade_shares_of_group"),
                (group_id,),
            )
            return [(str(row[0]), str(row[1])) for row in rows]

    async def unshare_from_group(
        self, resource_type: str, resource_id: str, group_id: str
    ) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/group_shares:delete_share"),
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
                sql("queries/group_shares:resource_ids_shared"),
                (group_id, resource_type),
            )
            return [r[0] for r in rows]

    async def get_user_shared_resource_groups(
        self, username: str, resource_type: str
    ) -> Dict[str, List[str]]:
        """Mapa recurso→grupos accesibles en una única consulta por usuario."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/group_shares:shares_visible_to_user"),
                (username, resource_type),
            )
        result: Dict[str, List[str]] = {}
        for resource_id, group_id in rows:
            result.setdefault(str(resource_id), []).append(str(group_id))
        return result

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
