"""Persistencia de packs de conocimiento y sus archivos catalogados."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.sql import sql
from app.storage import labels as label_index
from app.storage.db import open_db
from app.storage.skill_storage import ensure_origin_label
from app.utils.generators import generate_date, generate_id


def _pack_dict(row: Any) -> Dict[str, Any]:
    data = dict(row)
    raw_labels = data.get("labels")
    try:
        data["labels"] = (
            json.loads(raw_labels) if isinstance(raw_labels, str) else raw_labels
        ) or ["private"]
    except (TypeError, json.JSONDecodeError):
        data["labels"] = ["private"]
    data["labels"] = ensure_origin_label(
        [str(label) for label in data["labels"]], "community"
    )
    data["resource_type"] = "knowledge_pack"
    data["is_active"] = bool(data.get("is_active", True))
    data["file_count"] = int(data.get("file_count") or 0)
    data["size_bytes"] = int(data.get("size_bytes") or 0)
    data["source_mode"] = (
        "reference" if data.get("source_mode") == "reference" else "upload"
    )
    data["upload_status"] = str(data.get("upload_status") or "ready")
    return data


class KnowledgePackStorage:
    async def list(self, owner_id: Optional[str]) -> List[Dict[str, Any]]:
        query = """
            SELECT p.*,
                   COUNT(i.id) AS file_count,
                   COALESCE(SUM(i.size_bytes), 0) AS size_bytes
            FROM knowledge_packs p
            LEFT JOIN knowledge_items i ON i.pack_id=p.id
        """
        params: tuple = ()
        if owner_id is not None:
            query += " WHERE p.owner_id=?"
            params = (owner_id,)
        query += (
            " GROUP BY p.id HAVING COALESCE(p.upload_status, 'ready')='ready' "
            "ORDER BY p.created_at DESC"
        )
        async with open_db() as conn:
            rows = await conn.fetchall(query, params)
        return [_pack_dict(row) for row in rows]

    async def get(
        self,
        pack_id: str,
        owner_id: Optional[str] = None,
        *,
        include_items: bool = True,
    ) -> Optional[Dict[str, Any]]:
        query = """
            SELECT p.*,
                   COUNT(i.id) AS file_count,
                   COALESCE(SUM(i.size_bytes), 0) AS size_bytes
            FROM knowledge_packs p
            LEFT JOIN knowledge_items i ON i.pack_id=p.id
            WHERE p.id=?
        """
        params: tuple = (pack_id,)
        if owner_id is not None:
            query += " AND p.owner_id=?"
            params = (pack_id, owner_id)
        query += " GROUP BY p.id"
        async with open_db() as conn:
            row = await conn.fetchone(query, params)
            if row is None:
                return None
            pack = _pack_dict(row)
            if include_items:
                items = await conn.fetchall(
                    sql("queries/knowledge_packs:list_pack_files"),
                    (pack_id,),
                )
                pack["items"] = [dict(item) for item in items]
            return pack

    async def get_for_item(self, knowledge_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/knowledge_packs:get_file_with_pack"),
                (knowledge_id,),
            )
        return _pack_dict(row) if row else None

    async def item_ids(self, pack_id: str) -> List[str]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/knowledge_packs:pack_item_ids"),
                (pack_id,),
            )
        return [str(row[0]) for row in rows]

    async def create(
        self,
        *,
        owner_id: str,
        name: str,
        description: str,
        labels: List[str],
        items: List[Dict[str, Any]],
        source_mode: str = "upload",
        upload_status: str = "ready",
    ) -> Dict[str, Any]:
        pack_id = generate_id(16)
        now = generate_date()
        normalized_labels = ensure_origin_label(labels or ["private"], "community")
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/knowledge_packs:insert_pack"),
                    (
                        pack_id,
                        owner_id,
                        name,
                        description,
                        json.dumps(normalized_labels, ensure_ascii=False),
                        source_mode,
                        now,
                        upload_status,
                        now,
                        now,
                    ),
                )
                for item in items:
                    knowledge_id = generate_id(16)
                    content = str(item["content"])
                    await conn.execute(
                        sql("queries/knowledge_packs:insert_pack_item"),
                        (
                            knowledge_id,
                            owner_id,
                            "pack_item",
                            item["relative_path"].rsplit("/", 1)[-1],
                            item["relative_path"],
                            content,
                            len(content),
                            item.get("mime_type", ""),
                            int(item["size_bytes"]),
                            item["checksum"],
                            pack_id,
                            item["relative_path"],
                            item["kind"],
                            json.dumps(normalized_labels, ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
        pack = await self.get(pack_id)
        if pack is None:  # pragma: no cover - la transacción acaba de insertarlo
            raise RuntimeError("No se pudo recuperar el pack creado")
        return pack

    async def upsert_item(
        self,
        pack_id: str,
        owner_id: str,
        item: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        pack = await self.get(pack_id, owner_id, include_items=False)
        if pack is None or pack.get("upload_status") != "uploading":
            return None
        now = generate_date()
        relative_path = str(item["relative_path"])
        content = str(item["content"])
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/knowledge_packs:pack_item_by_path"),
                (pack_id, relative_path),
            )
            async with conn.transaction():
                if row:
                    knowledge_id = str(row[0])
                    await conn.execute(
                        sql("queries/knowledge_packs:update_pack_item_content"),
                        (
                            content,
                            len(content),
                            item["kind"],
                            item.get("mime_type", ""),
                            int(item["size_bytes"]),
                            item["checksum"],
                            now,
                            knowledge_id,
                        ),
                    )
                else:
                    knowledge_id = generate_id(16)
                    labels = json.dumps(pack.get("labels") or [], ensure_ascii=False)
                    await conn.execute(
                        sql("queries/knowledge_packs:insert_pack_item"),
                        (
                            knowledge_id,
                            owner_id,
                            "pack_item",
                            relative_path.rsplit("/", 1)[-1],
                            relative_path,
                            content,
                            len(content),
                            item.get("mime_type", ""),
                            int(item["size_bytes"]),
                            item["checksum"],
                            pack_id,
                            relative_path,
                            item["kind"],
                            labels,
                            now,
                            now,
                        ),
                    )
        await label_index.sync_labels(
            "knowledge", knowledge_id, owner_id, list(pack.get("labels") or [])
        )
        return {"id": knowledge_id, "relative_path": relative_path}

    async def complete_upload(
        self, pack_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        pack = await self.get(pack_id, owner_id, include_items=False)
        if pack is None or pack.get("upload_status") != "uploading":
            return None
        if not await self.item_ids(pack_id):
            return None
        now = generate_date()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/knowledge_packs:mark_pack_ready"),
                (now, now, pack_id, owner_id),
            )
            await conn.commit()
        return await self.get(pack_id)

    async def delete(self, pack_id: str, owner_id: Optional[str]) -> bool:
        pack = await self.get(pack_id, owner_id, include_items=False)
        if pack is None:
            return False
        async with open_db() as conn:
            async with conn.transaction():
                rows = await conn.fetchall(
                    sql("queries/knowledge_packs:pack_item_ids"),
                    (pack_id,),
                )
                item_ids = [str(row[0]) for row in rows]
                for resource_type, resource_id in [
                    ("knowledge_pack", pack_id),
                    *(("knowledge", item_id) for item_id in item_ids),
                ]:
                    await conn.execute(
                        sql("queries/knowledge_packs:delete_social_by_resource"),
                        (resource_type, resource_id),
                    )
                    await conn.execute(
                        sql("queries/knowledge_packs:delete_stars_by_resource"),
                        (resource_type, resource_id),
                    )
                    await conn.execute(
                        sql("queries/knowledge_packs:delete_shares_by_resource"),
                        (resource_type, resource_id),
                    )
                await conn.execute(
                    sql("queries/knowledge_packs:delete_pack_items"), (pack_id,)
                )
                await conn.execute(sql("queries/knowledge_packs:delete_pack"), (pack_id,))
        return True

    async def replace_items(
        self,
        pack_id: str,
        owner_id: Optional[str],
        items: List[Dict[str, Any]],
    ) -> Optional[Dict[str, int]]:
        """Synchronize a pack snapshot while preserving IDs for stable paths."""
        pack = await self.get(pack_id, owner_id, include_items=False)
        if pack is None:
            return None
        now = generate_date()
        labels = [str(value) for value in pack.get("labels") or []]
        encoded_labels = json.dumps(labels, ensure_ascii=False)
        async with open_db() as conn:
            existing_rows = await conn.fetchall(
                sql("queries/knowledge_packs:pack_items_for_sync"),
                (pack_id,),
            )
            existing = {str(row[1]): row for row in existing_rows}
            incoming_paths = {str(item["relative_path"]) for item in items}
            removed_rows = [
                row for path, row in existing.items() if path not in incoming_paths
            ]
            added_ids: List[str] = []
            async with conn.transaction():
                for row in removed_rows:
                    knowledge_id = str(row[0])
                    for table in (
                        "resource_social",
                        "resource_stars",
                        "resource_group_shares",
                    ):
                        await conn.execute(
                            f"DELETE FROM {table} WHERE resource_type='knowledge' "
                            "AND resource_id=?",
                            (knowledge_id,),
                        )
                    await conn.execute(
                        sql("queries/knowledge_packs:delete_item"), (knowledge_id,)
                    )
                for item in items:
                    relative_path = str(item["relative_path"])
                    row = existing.get(relative_path)
                    if "content" in item:
                        content = str(item["content"])
                    elif row is not None:
                        content = str(row[4])
                    else:
                        raise ValueError(
                            f"Falta el contenido del nuevo archivo {relative_path}"
                        )
                    if row is not None:
                        knowledge_id = str(row[0])
                        await conn.execute(
                            sql("queries/knowledge_packs:update_pack_item_content"),
                            (
                                content,
                                len(content),
                                item["kind"],
                                item.get("mime_type", ""),
                                int(item["size_bytes"]),
                                item["checksum"],
                                now,
                                knowledge_id,
                            ),
                        )
                    else:
                        knowledge_id = generate_id(16)
                        added_ids.append(knowledge_id)
                        await conn.execute(
                            sql("queries/knowledge_packs:insert_pack_item"),
                            (
                                knowledge_id,
                                pack["owner_id"],
                                "pack_item",
                                relative_path.rsplit("/", 1)[-1],
                                relative_path,
                                content,
                                len(content),
                                item.get("mime_type", ""),
                                int(item["size_bytes"]),
                                item["checksum"],
                                pack_id,
                                relative_path,
                                item["kind"],
                                encoded_labels,
                                now,
                                now,
                            ),
                        )
                await conn.execute(
                    sql("queries/knowledge_packs:touch_pack_sync"),
                    (now, now, pack_id),
                )
        for row in removed_rows:
            await label_index.clear_labels("knowledge", str(row[0]))
        for knowledge_id in added_ids:
            await label_index.sync_labels(
                "knowledge", knowledge_id, str(pack["owner_id"]), labels
            )
        changed = sum(
            1
            for item in items
            if str(item["relative_path"]) in existing
            and str(existing[str(item["relative_path"])][2]) != str(item["checksum"])
        )
        return {
            "added": len(added_ids),
            "updated": changed,
            "removed": len(removed_rows),
            "total": len(items),
        }

    async def update_labels(
        self, pack_id: str, owner_id: Optional[str], labels: List[str]
    ) -> bool:
        pack = await self.get(pack_id, owner_id, include_items=False)
        if pack is None:
            return False
        item_ids = await self.item_ids(pack_id)
        encoded = json.dumps(labels, ensure_ascii=False)
        now = generate_date()
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/knowledge_packs:update_pack_labels"),
                    (encoded, now, pack_id),
                )
                for item_id in item_ids:
                    await conn.execute(
                        sql("queries/knowledge_packs:update_item_labels"),
                        (encoded, now, item_id),
                    )
        await label_index.sync_labels(
            "knowledge_pack", pack_id, str(pack["owner_id"]), labels
        )
        for item_id in item_ids:
            await label_index.sync_labels(
                "knowledge", item_id, str(pack["owner_id"]), labels
            )
        return True

    async def update_metadata(
        self,
        pack_id: str,
        owner_id: Optional[str],
        *,
        name: str,
        description: str,
        labels: List[str],
    ) -> bool:
        """Update the pack and propagate its labels to every member."""
        pack = await self.get(pack_id, owner_id, include_items=False)
        if pack is None:
            return False
        item_ids = await self.item_ids(pack_id)
        encoded = json.dumps(labels, ensure_ascii=False)
        now = generate_date()
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/knowledge_packs:update_pack_metadata"),
                    (name, description, encoded, now, pack_id),
                )
                for item_id in item_ids:
                    await conn.execute(
                        sql("queries/knowledge_packs:update_item_labels"),
                        (encoded, now, item_id),
                    )
        await label_index.sync_labels(
            "knowledge_pack", pack_id, str(pack["owner_id"]), labels
        )
        for item_id in item_ids:
            await label_index.sync_labels(
                "knowledge", item_id, str(pack["owner_id"]), labels
            )
        return True
