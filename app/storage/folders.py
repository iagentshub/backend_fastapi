"""Group-scoped folders shared by agents, skills, knowledge and memory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.storage.db import IS_PG, open_db

VALID_SECTIONS = {"agents", "skill", "url", "document", "memory"}
_SHARED_RESOURCE_TYPES = {
    "agents": "agent",
    "skill": "skill",
    "url": "knowledge",
    "document": "knowledge",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _folder_dict(row: Any) -> Dict[str, Any]:
    data = dict(row)
    data["is_public"] = bool(data["is_public"])
    data["visibility"] = "public" if data["is_public"] else "private"
    return data


class FolderStorage:
    async def list(self, owner_id: str, section: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT id, owner_id, section, name, is_public, created_at, updated_at "
                "FROM resource_folders WHERE owner_id=? AND section=? ORDER BY name",
                (owner_id, section),
            )
        return [_folder_dict(row) for row in rows]

    async def get(self, folder_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT id, owner_id, section, name, is_public, created_at, updated_at "
                "FROM resource_folders WHERE id=? AND owner_id=?",
                (folder_id, owner_id),
            )
        return _folder_dict(row) if row else None

    async def create(self, owner_id: str, section: str, name: str) -> Dict[str, Any]:
        folder_id, now = uuid4().hex[:16], _now()
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO resource_folders "
                "(id, owner_id, section, name, is_public, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (folder_id, owner_id, section, name, False, now, now),
            )
            await conn.commit()
        return (await self.get(folder_id, owner_id)) or {}

    async def update(
        self, folder_id: str, owner_id: str, *, name: Optional[str] = None,
        is_public: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        current = await self.get(folder_id, owner_id)
        if not current:
            return None
        async with open_db() as conn:
            await conn.execute(
                "UPDATE resource_folders SET name=?, is_public=?, updated_at=? "
                "WHERE id=? AND owner_id=?",
                (
                    name if name is not None else current["name"],
                    is_public if is_public is not None else current["is_public"],
                    _now(), folder_id, owner_id,
                ),
            )
            await conn.commit()
        return await self.get(folder_id, owner_id)

    async def delete(self, folder_id: str, owner_id: str, cascade: bool) -> bool:
        if not await self.get(folder_id, owner_id):
            return False
        async with open_db() as conn:
            if cascade:
                resources = await conn.fetchall(
                    "SELECT resource_type, resource_id FROM resource_folder_items "
                    "WHERE folder_id=? AND owner_id=?",
                    (folder_id, owner_id),
                )
                for resource in resources:
                    resource_type = resource["resource_type"]
                    resource_id = resource["resource_id"]
                    if resource_type == "agents":
                        await conn.execute(
                            "DELETE FROM agents WHERE id=? AND owner_id=? AND scope!='public'",
                            (resource_id, owner_id),
                        )
                    elif resource_type == "skill":
                        await conn.execute(
                            "DELETE FROM skills WHERE id=? AND owner_id=? AND scope!='public'",
                            (resource_id, owner_id),
                        )
                    elif resource_type in {"url", "document"}:
                        await conn.execute(
                            "DELETE FROM knowledge_items WHERE id=? AND owner_id=?",
                            (resource_id, owner_id),
                        )
                    elif resource_type == "memory":
                        await conn.execute(
                            "DELETE FROM memory_files WHERE id=? AND owner_id=?",
                            (resource_id.removesuffix(".md"), owner_id),
                        )
                    shared_type = _SHARED_RESOURCE_TYPES.get(resource_type)
                    if shared_type:
                        await conn.execute(
                            "DELETE FROM resource_group_shares "
                            "WHERE resource_type=? AND resource_id=?",
                            (shared_type, resource_id),
                        )
                        await conn.execute(
                            "DELETE FROM resource_social "
                            "WHERE resource_type=? AND resource_id=? AND owner=?",
                            (shared_type, resource_id, owner_id),
                        )
                        await conn.execute(
                            "DELETE FROM resource_stars "
                            "WHERE resource_type=? AND resource_id=?",
                            (shared_type, resource_id),
                        )
                    if resource_type == "agents":
                        await conn.execute(
                            "DELETE FROM user_agent_preferences WHERE agent_id=?",
                            (resource_id,),
                        )
            await conn.execute(
                "DELETE FROM resource_folder_items WHERE folder_id=? AND owner_id=?",
                (folder_id, owner_id),
            )
            await conn.execute(
                "DELETE FROM resource_social "
                "WHERE resource_type='knowledge' AND resource_id=? AND owner=?",
                (folder_id, owner_id),
            )
            await conn.execute(
                "DELETE FROM resource_stars "
                "WHERE resource_type='knowledge' AND resource_id=?",
                (folder_id,),
            )
            await conn.execute(
                "DELETE FROM resource_folders WHERE id=? AND owner_id=?",
                (folder_id, owner_id),
            )
            await conn.commit()
        return True

    async def assign(
        self, owner_id: str, resource_type: str, resource_id: str,
        folder_id: Optional[str],
    ) -> None:
        if not folder_id:
            async with open_db() as conn:
                await conn.execute(
                    "DELETE FROM resource_folder_items "
                    "WHERE owner_id=? AND resource_type=? AND resource_id=?",
                    (owner_id, resource_type, resource_id),
                )
                await conn.commit()
            return
        if folder_id:
            folder = await self.get(folder_id, owner_id)
            if not folder or folder["section"] != resource_type:
                raise ValueError("Carpeta incompatible con el recurso")
        async with open_db() as conn:
            if IS_PG:
                await conn.execute(
                    "INSERT INTO resource_folder_items "
                    "(owner_id, resource_type, resource_id, folder_id) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (owner_id, resource_type, resource_id) "
                    "DO UPDATE SET folder_id=EXCLUDED.folder_id",
                    (owner_id, resource_type, resource_id, folder_id),
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO resource_folder_items "
                    "(owner_id, resource_type, resource_id, folder_id) VALUES (?, ?, ?, ?)",
                    (owner_id, resource_type, resource_id, folder_id),
                )
            await conn.commit()

    async def remove_resource(
        self, owner_id: str, resource_type: str, resource_id: str
    ) -> None:
        """Remove a folder association after its resource has been deleted."""
        async with open_db() as conn:
            await conn.execute(
                "DELETE FROM resource_folder_items "
                "WHERE owner_id=? AND resource_type=? AND resource_id=?",
                (owner_id, resource_type, resource_id),
            )
            await conn.commit()

    async def folder_for(
        self, owner_id: str, resource_type: str, resource_id: str
    ) -> Optional[str]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT folder_id FROM resource_folder_items "
                "WHERE owner_id=? AND resource_type=? AND resource_id=?",
                (owner_id, resource_type, resource_id),
            )
        return row["folder_id"] if row else None

    async def enrich(
        self, items: List[Dict[str, Any]], owner_id: str, resource_type: str
    ) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT resource_id, folder_id FROM resource_folder_items "
                "WHERE owner_id=? AND resource_type=?",
                (owner_id, resource_type),
            )
        folders = {row["resource_id"]: row["folder_id"] for row in rows}
        return [{**item, "folder_id": folders.get(item.get("id") or item.get("filename"))} for item in items]

    async def enrich_items(
        self,
        items: List[Dict[str, Any]],
        *,
        default_owner: str,
        resource_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Attach folder ids with one query, including mixed owners and sections."""
        if not items:
            return []
        keys = {
            (
                str(item.get("owner_id") or default_owner),
                resource_type or str(item.get("type") or ""),
                str(item.get("id") or item.get("filename") or ""),
            )
            for item in items
        }
        keys = {
            key for key in keys
            if key[0] and key[1] in VALID_SECTIONS and key[2]
        }
        if not keys:
            return [{**item, "folder_id": None} for item in items]
        owners = sorted({key[0] for key in keys})
        types = sorted({key[1] for key in keys})
        owner_marks = ", ".join("?" for _ in owners)
        type_marks = ", ".join("?" for _ in types)
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT owner_id, resource_type, resource_id, folder_id "
                "FROM resource_folder_items "
                f"WHERE owner_id IN ({owner_marks}) "
                f"AND resource_type IN ({type_marks})",
                (*owners, *types),
            )
        folders = {
            (row["owner_id"], row["resource_type"], row["resource_id"]): row["folder_id"]
            for row in rows
        }
        return [
            {
                **item,
                "folder_id": folders.get(
                    (
                        str(item.get("owner_id") or default_owner),
                        resource_type or str(item.get("type") or ""),
                        str(item.get("id") or item.get("filename") or ""),
                    )
                ),
            }
            for item in items
        ]
