"""La fuente en sí y los recursos que ha materializado.

Va como mixin y no como clase aparte para que los métodos se sigan llamando
entre sí por `self`, que es como estaban escritos: ningún cuerpo cambió al
moverlos.
"""


from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.official_source import INTERNAL_SOURCE_ID, OfficialSource
from app.sql import sql
from app.storage.db import AsyncConn, open_db

# Tablas de recurso que pueden llevar contenido oficial, con su tipo lógico.
from app.storage.official_source_storage._shared import (
    OFFICIAL_RESOURCE_TABLES,
    SOURCE_RESOURCE_TYPES,
)
from app.utils import now_iso
from app.utils.generators import generate_id


class _SourcesMixin:
    async def list_sources(self) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/official_sources:list_all")
            )
        return [OfficialSource(**dict(row)).as_dict() for row in rows]

    async def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:get_by_id"), (source_id,)
            )
        return OfficialSource(**dict(row)).as_dict() if row else None

    async def find_by_repository(self, repository_url: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:get_by_url"),
                (repository_url,),
            )
        return OfficialSource(**dict(row)).as_dict() if row else None

    async def save_source(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Alta o actualización por repositorio, que es la clave natural."""
        existing = await self.find_by_repository(str(data["repository_url"]))
        source_id = str((existing or {}).get("id") or data.get("id") or generate_id())
        now = now_iso()
        async with open_db() as conn:
            if existing:
                await conn.execute(
                    sql("queries/official_sources:update_from_repo"),
                    (
                        data["name"],
                        data.get("description", ""),
                        data.get("repository_owner", ""),
                        data.get("repository_name", ""),
                        data.get("provider", "github"),
                        data.get("repository_path", ""),
                        data.get("owner_id"),
                        data.get("default_branch", "main"),
                        data.get("tracking_mode", "release"),
                        data.get("tracking_ref", "main"),
                        data.get("import_mode", "deterministic"),
                        data.get("llm_connection_id"),
                        data.get("license", ""),
                        now,
                        source_id,
                    ),
                )
            else:
                await conn.execute(
                    sql("queries/official_sources:insert_full"),
                    (
                        source_id,
                        data["name"],
                        data.get("description", ""),
                        data["repository_url"],
                        data.get("repository_owner", ""),
                        data.get("repository_name", ""),
                        data.get("provider", "github"),
                        data.get("repository_path", ""),
                        data.get("owner_id"),
                        data.get("default_branch", "main"),
                        data.get("tracking_mode", "release"),
                        data.get("tracking_ref", "main"),
                        data.get("import_mode", "deterministic"),
                        data.get("llm_connection_id"),
                        data.get("license", ""),
                        now,
                        now,
                    ),
                )
            await conn.commit()
        result = await self.get_source(source_id)
        assert result is not None
        return result

    async def update_source(
        self, source_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not await self.get_source(source_id):
            return None
        async with open_db() as conn:
            duplicate = await conn.fetchone(
                sql("queries/official_sources:url_taken_by_other"),
                (data["repository_url"], source_id),
            )
            if duplicate:
                raise ValueError("repository_already_registered")
            await conn.execute(
                sql("queries/official_sources:update_fields"),
                (
                    data["name"],
                    data.get("description", ""),
                    data["repository_url"],
                    data.get("repository_owner", ""),
                    data.get("repository_name", ""),
                    data.get("provider", "github"),
                    data.get("repository_path", ""),
                    data.get("default_branch", "main"),
                    data.get("tracking_mode", "release"),
                    data.get("tracking_ref", "main"),
                    data.get("license", ""),
                    now_iso(),
                    source_id,
                ),
            )
            await conn.commit()
        return await self.get_source(source_id)

    async def mark_sync(
        self,
        source_id: str,
        *,
        version: Optional[str] = None,
        commit_sha: Optional[str] = None,
        error: Optional[str] = None,
        state: str = "idle",
    ) -> None:
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:update_sync_result"),
                (now, error, version, commit_sha, state, now, source_id),
            )
            await conn.commit()

    async def acquire_sync_lock(self, source_id: str, base_commit_sha: str) -> bool:
        """Bloqueo compare-and-set; también rechaza un borrador obsoleto."""
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:claim_applying"),
                (now_iso(), source_id, base_commit_sha),
            )
            await conn.commit()
        return row is not None

    async def delete_source(self, source_id: str) -> bool:
        if not await self.get_source(source_id):
            return False
        async with open_db() as conn:
            await conn.execute(sql("queries/official_sources:delete_source"), (source_id,))
            await conn.commit()
        return True

    async def ensure_internal_source(self) -> Dict[str, Any]:
        """Fuente para lo que un admin marca como oficial sin repositorio."""
        existing = await self.get_source(INTERNAL_SOURCE_ID)
        if existing:
            return existing
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:insert_minimal"),
                (
                    INTERNAL_SOURCE_ID,
                    "iAgents Hub",
                    "Contenido marcado como oficial desde el panel",
                    f"internal://{INTERNAL_SOURCE_ID}",
                    "internal",
                    INTERNAL_SOURCE_ID,
                    "branch",
                    "main",
                    now,
                    now,
                ),
            )
            await conn.commit()
        result = await self.get_source(INTERNAL_SOURCE_ID)
        assert result is not None
        return result

    async def mark_resource(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        *,
        source_id: Optional[str],
        component_id: Optional[str] = None,
        source_path: str = "",
        content_hash: str = "",
        commit_sha: str = "",
        explicitly_selected: bool = True,
        conn: Optional[AsyncConn] = None,
    ) -> None:
        """Marca (o desmarca, con ``source_id=None``) un recurso como oficial.

        Las columnas se escriben aquí y no en el storage del recurso: son
        metadatos de gobierno del catálogo, no del objeto, y así ningún
        guardado normal puede inventárselos.
        """
        if resource_type not in SOURCE_RESOURCE_TYPES:
            raise ValueError(f"tipo de recurso sin tabla oficial: {resource_type!r}")
        if source_id and not component_id:
            raise ValueError("component_id_required")
        if conn is not None:
            await self._mark_resource_conn(
                conn,
                resource_type,
                resource_id,
                owner_id,
                source_id=source_id,
                component_id=component_id,
                source_path=source_path,
                content_hash=content_hash,
                commit_sha=commit_sha,
                explicitly_selected=explicitly_selected,
            )
            return
        async with open_db() as own_conn:
            await self._mark_resource_conn(
                own_conn,
                resource_type,
                resource_id,
                owner_id,
                source_id=source_id,
                component_id=component_id,
                source_path=source_path,
                content_hash=content_hash,
                commit_sha=commit_sha,
                explicitly_selected=explicitly_selected,
            )
            await own_conn.commit()

    async def _mark_resource_conn(
        self,
        conn: AsyncConn,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        *,
        source_id: Optional[str],
        component_id: Optional[str],
        source_path: str,
        content_hash: str,
        commit_sha: str,
        explicitly_selected: bool,
    ) -> None:
        table = OFFICIAL_RESOURCE_TABLES.get(resource_type)
        if table:
            await conn.execute(
                f"UPDATE {table} SET official_source_id=?, official_component_id=? "
                "WHERE id=? AND owner_id=?",
                (source_id, component_id if source_id else None, resource_id, owner_id),
            )
        if source_id:
            now = now_iso()
            await conn.execute(
                sql("queries/official_sources:upsert_link"),
                (
                    source_id,
                    component_id,
                    resource_type,
                    resource_id,
                    owner_id,
                    source_path,
                    content_hash,
                    commit_sha,
                    int(explicitly_selected),
                    now,
                    now,
                ),
            )
        else:
            await conn.execute(
                sql("queries/official_sources:delete_link_by_resource"),
                (resource_type, resource_id, owner_id),
            )

    async def find_resource(
        self, source_id: str, component_id: str
    ) -> Optional[Dict[str, Any]]:
        """Recurso ya materializado para un componente, si sigue existiendo."""
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:get_link"),
                (source_id, component_id),
            )
        if not row:
            return None
        result = dict(row)
        result["owner_id"] = result.pop("resource_owner_id")
        return result

    async def list_resources(self, source_id: str) -> List[Dict[str, Any]]:
        """Recursos materializados por una fuente, de todos los tipos."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/official_sources:list_links"),
                (source_id,),
            )
        return [
            {
                **dict(row),
                "component_id": row["component_key"],
                "owner_id": row["resource_owner_id"],
            }
            for row in rows
        ]

    async def get_origin(
        self, resource_type: str, resource_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        params: tuple[Any, ...] = (resource_type, resource_id)
        owner_filter = ""
        if owner_id is not None:
            owner_filter = " AND l.resource_owner_id=?"
            params = (*params, owner_id)
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT l.*,s.name AS source_name,s.repository_url,s.provider,"
                "s.repository_path,s.owner_id AS source_owner_id,s.last_commit_sha,"
                "s.sync_state FROM resource_source_links l "
                "JOIN official_sources s ON s.id=l.source_id "
                "WHERE l.resource_type=? AND l.resource_id=?" + owner_filter,
                params,
            )
        return dict(row) if row else None

    async def set_owner(self, source_id: str, owner_id: str) -> bool:
        if not await self.get_source(source_id):
            return False
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:set_owner"),
                (owner_id, now_iso(), source_id),
            )
            await conn.commit()
        return True

    async def transfer_owner(self, source_id: str, owner_id: str) -> bool:
        """Transfiere fuente y objetos originales; nunca toca copias ajenas."""
        source = await self.get_source(source_id)
        if not source:
            return False
        links = await self.list_resources(source_id)
        table_by_type = {
            **OFFICIAL_RESOURCE_TABLES,
            "memory": "memory_files",
        }
        async with open_db() as conn:
            async with conn.transaction():
                for item in links:
                    table = table_by_type[str(item["resource_type"])]
                    conflict = await conn.fetchone(
                        f"SELECT 1 FROM {table} WHERE id=? AND owner_id=?",
                        (item["resource_id"], owner_id),
                    )
                    if conflict and item["owner_id"] != owner_id:
                        raise ValueError("owner_transfer_resource_conflict")
                for item in links:
                    old_owner = str(item["owner_id"])
                    resource_type = str(item["resource_type"])
                    resource_id = str(item["resource_id"])
                    table = table_by_type[resource_type]
                    await conn.execute(
                        f"UPDATE {table} SET owner_id=? WHERE id=? AND owner_id=?",
                        (owner_id, resource_id, old_owner),
                    )
                    await conn.execute(
                        sql("queries/official_sources:relabel_labels_owner"),
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                    await conn.execute(
                        sql("queries/official_sources:relabel_social_owner"),
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                    await conn.execute(
                        sql("queries/official_sources:relabel_versions_owner"),
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                await conn.execute(
                    sql("queries/official_sources:relabel_links_owner"),
                    (owner_id, source_id),
                )
                await conn.execute(
                    sql("queries/official_sources:set_owner"),
                    (owner_id, now_iso(), source_id),
                )
        return True
