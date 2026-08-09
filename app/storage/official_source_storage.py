"""Persistencia de las fuentes oficiales.

Solo la fuente: lo que trae se guarda como recurso normal en agents/skills/…
con ``official_source_id`` apuntando aquí (ver services/official_source_sync).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.official_source import INTERNAL_SOURCE_ID, OfficialSource
from app.storage.db import open_db
from app.utils import now_iso
from app.utils.generators import generate_id

# Tablas de recurso que pueden llevar contenido oficial, con su tipo lógico.
OFFICIAL_RESOURCE_TABLES: Dict[str, str] = {
    "agent": "agents",
    "skill": "skills",
    "prompt": "prompts",
    "tool": "tools",
    "knowledge": "knowledge_items",
    "workflow": "agent_workflows",
}


class OfficialSourceStorage:
    async def list_sources(self) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM official_sources ORDER BY lower(name)"
            )
        return [OfficialSource(**dict(row)).as_dict() for row in rows]

    async def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM official_sources WHERE id=?", (source_id,)
            )
        return OfficialSource(**dict(row)).as_dict() if row else None

    async def find_by_repository(self, repository_url: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM official_sources WHERE repository_url=?",
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
                    "UPDATE official_sources SET name=?, description=?, "
                    "repository_owner=?, repository_name=?, tracking_mode=?, "
                    "tracking_ref=?, license=?, updated_at=? WHERE id=?",
                    (
                        data["name"],
                        data.get("description", ""),
                        data.get("repository_owner", ""),
                        data.get("repository_name", ""),
                        data.get("tracking_mode", "release"),
                        data.get("tracking_ref", "main"),
                        data.get("license", ""),
                        now,
                        source_id,
                    ),
                )
            else:
                await conn.execute(
                    "INSERT INTO official_sources "
                    "(id,name,description,repository_url,repository_owner,"
                    "repository_name,tracking_mode,tracking_ref,license,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        source_id,
                        data["name"],
                        data.get("description", ""),
                        data["repository_url"],
                        data.get("repository_owner", ""),
                        data.get("repository_name", ""),
                        data.get("tracking_mode", "release"),
                        data.get("tracking_ref", "main"),
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
                "SELECT id FROM official_sources WHERE repository_url=? AND id<>?",
                (data["repository_url"], source_id),
            )
            if duplicate:
                raise ValueError("repository_already_registered")
            await conn.execute(
                "UPDATE official_sources SET name=?, description=?, repository_url=?, "
                "repository_owner=?, repository_name=?, tracking_mode=?, "
                "tracking_ref=?, license=?, updated_at=? WHERE id=?",
                (
                    data["name"],
                    data.get("description", ""),
                    data["repository_url"],
                    data.get("repository_owner", ""),
                    data.get("repository_name", ""),
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
        error: Optional[str] = None,
    ) -> None:
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                "UPDATE official_sources SET latest_checked_at=?, last_sync_error=?, "
                "last_version=COALESCE(?, last_version), updated_at=? WHERE id=?",
                (now, error, version, now, source_id),
            )
            await conn.commit()

    async def delete_source(self, source_id: str) -> bool:
        async with open_db() as conn:
            cursor = await conn.execute(
                "DELETE FROM official_sources WHERE id=?", (source_id,)
            )
            await conn.commit()
        return bool(getattr(cursor, "rowcount", 0))

    async def ensure_internal_source(self) -> Dict[str, Any]:
        """Fuente para lo que un admin marca como oficial sin repositorio."""
        existing = await self.get_source(INTERNAL_SOURCE_ID)
        if existing:
            return existing
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO official_sources "
                "(id,name,description,repository_url,tracking_mode,tracking_ref,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    INTERNAL_SOURCE_ID,
                    "iAgents Hub",
                    "Contenido marcado como oficial desde el panel",
                    f"internal://{INTERNAL_SOURCE_ID}",
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
    ) -> None:
        """Marca (o desmarca, con ``source_id=None``) un recurso como oficial.

        Las columnas se escriben aquí y no en el storage del recurso: son
        metadatos de gobierno del catálogo, no del objeto, y así ningún
        guardado normal puede inventárselos.
        """
        table = OFFICIAL_RESOURCE_TABLES.get(resource_type)
        if not table:
            raise ValueError(f"tipo de recurso sin tabla oficial: {resource_type!r}")
        async with open_db() as conn:
            await conn.execute(
                f"UPDATE {table} SET official_source_id=?, official_component_id=? "
                "WHERE id=? AND owner_id=?",
                (source_id, component_id if source_id else None, resource_id, owner_id),
            )
            await conn.commit()

    async def find_resource(
        self, source_id: str, component_id: str
    ) -> Optional[Dict[str, Any]]:
        """Recurso ya materializado para un componente, si sigue existiendo."""
        async with open_db() as conn:
            for resource_type, table in OFFICIAL_RESOURCE_TABLES.items():
                row = await conn.fetchone(
                    f"SELECT id, owner_id FROM {table} "
                    "WHERE official_source_id=? AND official_component_id=?",
                    (source_id, component_id),
                )
                if row:
                    return {
                        "resource_type": resource_type,
                        "resource_id": row["id"],
                        "owner_id": row["owner_id"],
                    }
        return None

    async def list_resources(self, source_id: str) -> List[Dict[str, Any]]:
        """Recursos materializados por una fuente, de todos los tipos."""
        result: List[Dict[str, Any]] = []
        async with open_db() as conn:
            for resource_type, table in OFFICIAL_RESOURCE_TABLES.items():
                rows = await conn.fetchall(
                    f"SELECT id, owner_id, official_component_id FROM {table} "
                    "WHERE official_source_id=?",
                    (source_id,),
                )
                result.extend(
                    {
                        "resource_type": resource_type,
                        "resource_id": row["id"],
                        "owner_id": row["owner_id"],
                        "component_id": row["official_component_id"],
                    }
                    for row in rows
                )
        return result
