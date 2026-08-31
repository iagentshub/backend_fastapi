"""Immutable snapshots for editable hub resources."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.config.resource_versions import MAX_VERSIONS_PER_RESOURCE
from app.sql import sql
from app.storage import db as _db
from app.storage.db import AsyncConn, open_db
from app.utils.generators import generate_date as _now
from app.utils.generators import generate_id


class ResourceVersionStorage:
    async def create(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        snapshot: Dict[str, Any],
        created_by: str,
        reason: str = "save",
        *,
        conn: Optional[AsyncConn] = None,
    ) -> Dict[str, Any]:
        async def write(target: AsyncConn) -> Dict[str, Any]:
            latest = await target.fetchval(
                sql("queries/resource_versions:max_version"),
                (resource_type, resource_id, owner_id),
            )
            version = int(latest or 0) + 1
            item = {
                "id": generate_id(32),
                "resource_type": resource_type,
                "resource_id": resource_id,
                "owner_id": owner_id,
                "version": version,
                "snapshot": snapshot,
                "created_by": created_by,
                "reason": reason,
                "created_at": _now(),
            }
            await target.execute(
                sql("queries/resource_versions:insert_version"),
                (
                    item["id"],
                    resource_type,
                    resource_id,
                    owner_id,
                    version,
                    json.dumps(snapshot, ensure_ascii=False),
                    created_by,
                    reason,
                    item["created_at"],
                ),
            )
            artifact_sha = (
                str(snapshot.get("binary_sha256") or "")
                if resource_type == "tool"
                else ""
            )
            if artifact_sha:
                query = (
                    "queries/tools:retain_version_artifact_pg"
                    if _db.IS_PG
                    else "queries/tools:retain_version_artifact_sqlite"
                )
                await target.execute(sql(query), (item["id"], artifact_sha))
            await self._prune(target, resource_type, resource_id, owner_id, version)
            return item

        if conn is not None:
            return await write(conn)
        async with open_db() as own_conn:
            item = await write(own_conn)
            await own_conn.commit()
        return item

    async def _prune(
        self,
        target: AsyncConn,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        version: int,
    ) -> None:
        """Deja solo las últimas `MAX_VERSIONS_PER_RESOURCE` de este recurso.

        Va en la misma transacción que el archivado, así que o se archiva y se
        poda, o no pasa ninguna de las dos cosas. Antes no había ningún camino
        que borrase de esta tabla salvo el RGPD al eliminar la cuenta entera y
        la resincronización de una fuente oficial: era append-only por accidente
        y crecía con el botón de guardar, no con la actividad de un tercero.
        """
        corte = version - MAX_VERSIONS_PER_RESOURCE
        if corte < 1:
            return
        podadas = await target.fetchall(
            sql("queries/resource_versions:prune_versions"),
            (resource_type, resource_id, owner_id, corte),
        )
        if podadas and resource_type == "tool":
            # El binario queda huérfano cuando se va la última versión que lo
            # retenía. La consulta ya existe; aquí solo se le da otro momento
            # para pasar, además del borrado de la tool.
            await target.execute(sql("queries/tools:delete_orphan_artifacts"))

    async def list(
        self, resource_type: str, resource_id: str, owner_id: str
    ) -> List[Dict[str, Any]]:
        """Metadatos de las versiones, de la más reciente a la más antigua.

        Sin cota: llegó a estar paginada con `OffsetPage`, pero la migración a
        paginación por cursor retiró ese mecanismo del backend y este endpoint
        no está entre los que se migraron. Devuelve solo metadatos —id, versión,
        autor, motivo, fecha—, así que no arrastra los snapshots, y el tope por
        recurso de `_prune` acota cuántas filas puede haber.

        ponytail: cuando se pagine, va con el mismo cursor que el resto. El
        desempate del ORDER BY ya es único —`version` lo es dentro de un
        recurso—, que es la mitad difícil.
        """
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/resource_versions:list_versions"),
                (resource_type, resource_id, owner_id),
            )
        return [dict(row) for row in rows]

    async def get(
        self, resource_type: str, resource_id: str, owner_id: str, version: int
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/resource_versions:get_version"),
                (resource_type, resource_id, owner_id, version),
            )
        if not row:
            return None
        item = dict(row)
        item["snapshot"] = json.loads(item["snapshot"])
        return item
