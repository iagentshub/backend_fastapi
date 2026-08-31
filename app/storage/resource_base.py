"""Base común de los almacenes de recursos gestionados por el usuario.

Aporta el comportamiento transversal a los recursos gestionados por el usuario:

- activación / desactivación para los recursos que exponen ese estado;
- sincronización del índice transversal de etiquetas (``resource_labels``).

El SQL específico de cada tabla (blob vs columnas, PK simple vs compuesta,
cifrado…) permanece en cada subclase; esta base solo unifica lo que es idéntico
en todas y que, al estar duplicado, ya provocó un fallo de aislamiento.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

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
    #: Identificadores SQL del listado, por ámbito. Los nombres de sección ya
    #: eran uniformes en las tres subclases con ámbito, que es justo lo que hace
    #: posible subir el método aquí.
    #:
    #: Se declaran como literales en la subclase, y no se montan con un
    #: `f"queries/{ns}:list_public"`, porque dos guardas leen el código buscando
    #: esas cadenas: una comprueba que todo identificador resuelve y la otra que
    #: ninguna sección se queda sin consumidor. Con el nombre construido en
    #: tiempo de ejecución, las doce secciones parecerían muertas y una errata
    #: no la vería nadie hasta la primera llamada.
    list_queries: Dict[str, str] = {}

    async def _migrate_legacy_data(self) -> None:
        """Sin migración legacy por defecto; las subclases que la necesitan
        (agentes, skills, conexiones desde JSON) la sobreescriben."""
        return None

    async def get_any(
        self, resource_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """El recurso en cualquier ámbito: primero público, luego privado.

        Estaba escrito tres veces —skill, prompt y tool— con el mismo bucle de
        siete líneas y solo el sustantivo cambiado. Los almacenes sin ámbito
        (workflows, orquestaciones) tienen el suyo propio y lo sobrescriben.
        """
        for scope in ("public", "private"):
            result = await self.get(scope, resource_id, owner_id=owner_id)  # type: ignore[attr-defined]
            if result:
                return result
        return None

    async def list(
        self, scope: str = "all", owner_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Listado por ámbito. Idéntico en las tres salvo el prefijo SQL.

        Un cambio de contrato en el listado se escribe una vez en vez de tres,
        que es exactamente la divergencia que este repo documenta haber sufrido
        en el cliente antes de unificar sus repositorios en
        `ScopedResourceRepository`.
        """
        await self._ensure_migrated()
        from app.storage.db import open_db

        if scope == "public":
            consulta, params = self.list_queries["public"], ()
        elif scope == "private" and owner_id:
            consulta, params = self.list_queries["private_by_owner"], (owner_id,)
        elif scope == "private":
            consulta, params = self.list_queries["private"], ()
        else:
            consulta, params = self.list_queries["all"], ()

        async with open_db() as conn:
            rows = await conn.fetchall(sql(consulta), params)
        return [self._row_to_dict(r, include_content=False) for r in rows]  # type: ignore[attr-defined]

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
