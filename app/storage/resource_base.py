"""Base común de los almacenes de recursos gestionados por el usuario.

Aporta el comportamiento transversal a agentes, skills, conexiones,
conocimientos y workflows:

- borrado suave / reactivación (``set_active``) sobre ``is_active`` +
  ``deactivated_at``, con el predicado ``owner_id`` incorporado;
- sincronización del índice transversal de etiquetas (``resource_labels``).

El SQL específico de cada tabla (blob vs columnas, PK simple vs compuesta,
cifrado…) permanece en cada subclase; esta base solo unifica lo que es idéntico
en todas y que, al estar duplicado, ya provocó un fallo de aislamiento.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from app.sql import sql
from app.storage import labels as _labels
from app.storage.migration import LegacyMigrationStorage
from app.utils.generators import generate_date

if TYPE_CHECKING:
    from app.storage.db import AsyncConn


class ResourceStorage(LegacyMigrationStorage):
    #: Nombre de la tabla SQL. Cada subclase lo define.
    table: str = ""
    #: Tipo canónico de recurso (ver app.models.resource_types). Subclase.
    resource_type: str = ""

    async def _migrate_legacy_data(self) -> None:
        """Sin migración legacy por defecto; las subclases que la necesitan
        (agentes, skills, conexiones desde JSON) la sobreescriben."""
        return None

    async def set_active(
        self, resource_id: str, owner_id: Optional[str], active: bool
    ) -> bool:
        """Activa o desactiva (borrado suave) un recurso.

        owner_id=None → sin filtro de propietario (uso admin).
        Devuelve False si no existe la fila objetivo.
        """
        await self._ensure_migrated()
        from app.storage.db import open_db

        deactivated_at = None if active else generate_date()
        flag = 1 if active else 0
        async with open_db() as conn:
            if owner_id is not None:
                row = await conn.fetchone(
                    f"SELECT id FROM {self.table} WHERE id=? AND owner_id=? LIMIT 1",
                    (resource_id, owner_id),
                )
                if not row:
                    return False
                await conn.execute(
                    f"UPDATE {self.table} SET is_active=?, deactivated_at=? "
                    "WHERE id=? AND owner_id=?",
                    (flag, deactivated_at, resource_id, owner_id),
                )
            else:
                row = await conn.fetchone(
                    f"SELECT id FROM {self.table} WHERE id=? LIMIT 1", (resource_id,)
                )
                if not row:
                    return False
                await conn.execute(
                    f"UPDATE {self.table} SET is_active=?, deactivated_at=? WHERE id=?",
                    (flag, deactivated_at, resource_id),
                )
            await conn.commit()
        return True

    async def sync_labels(
        self,
        resource_id: str,
        owner_id: Optional[str],
        labels: List[str],
        *,
        conn: Optional["AsyncConn"] = None,
    ) -> None:
        """Refleja las etiquetas del recurso en el índice transversal."""
        if conn is not None:
            from app.models.resource_types import normalize_resource_type

            resource_type = normalize_resource_type(self.resource_type)
            # Las mismas dos sentencias que usa storage/labels.py: comparten
            # sección para que el índice transversal se escriba igual desde los
            # dos sitios.
            await conn.execute(
                sql("queries/labels:delete_labels"), (resource_type, resource_id)
            )
            for label in sorted({item.strip() for item in labels if item.strip()}):
                await conn.execute(
                    sql("queries/labels:insert_label"),
                    (resource_type, resource_id, owner_id or "", label),
                )
            return
        await _labels.sync_labels(self.resource_type, resource_id, owner_id, labels)

    async def clear_labels(self, resource_id: str) -> None:
        """Elimina del índice las etiquetas de un recurso borrado."""
        await _labels.clear_labels(self.resource_type, resource_id)
