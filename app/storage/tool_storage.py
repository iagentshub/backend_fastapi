"""Persistencia de definiciones Tool y artefactos nativos deduplicados."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Dict, List, Optional

from app.config.tool_runtimes import TOOL_RUNTIME_BY_VALUE, TOOL_RUNTIMES
from app.sql import sql

# db se importa DOS veces a propósito: ver app/storage/_storage_helpers.py.
from app.storage import db as _db
from app.storage._storage_helpers import _PUBLIC_OWNER, _slug
from app.storage.db import AsyncConn, open_db
from app.storage.db_migrations import _compact_resource_data
from app.storage.resource_base import ResourceStorage
from app.storage.scoped_resource_page import ScopedResourcePageSpec
from app.storage.scoped_resource_pagination import ScopedResourcePaginationMixin

# Catálogo de labels compartido; vive en skill_storage (ver comentario allí).
from app.storage.skill_storage import SKILL_LABELS, ensure_origin_label
from app.utils import now_iso as _now
from app.utils.generators import generate_id


class ToolStorage(ScopedResourcePaginationMixin, ResourceStorage):
    """Async DB-backed tool storage (SQLite / PostgreSQL).

    Sin migración legacy de ficheros (a diferencia de Skill): se instancia
    sin argumentos, igual que PromptStorage.
    """

    table = "tools"
    resource_type = "tool"
    # Los cuatro identificadores del listado, literales para que las guardas
    # de SQL los sigan viendo. La lógica del método vive en ResourceStorage.
    list_queries = {
        "public": "queries/tools:list_public",
        "private_by_owner": "queries/tools:list_private_by_owner",
        "private": "queries/tools:list_private",
        "all": "queries/tools:list_all",
    }

    def _page_spec(self) -> ScopedResourcePageSpec:
        return ScopedResourcePageSpec(
            table=self.table,
            columns=(
                "resource_row.id, resource_row.owner_id, resource_row.name, "
                "resource_row.language, resource_row.scope, resource_row.data, "
                "resource_row.binary_filename, resource_row.binary_size, "
                "resource_row.binary_uploaded_at, resource_row.created_at, "
                "CASE WHEN resource_row.content <> '' THEN 1 ELSE 0 END AS has_content, "
                "resource_row.updated_at, resource_row.is_active, "
                "resource_row.deactivated_at"
            ),
            resource_type=self.resource_type,
            decode=lambda row: self._row_to_dict(row, include_content=False),
        )

    async def _upsert(
        self, conn: Any, tool_id: str, owner_id: str, scope: str, data: Dict[str, Any]
    ) -> None:
        name = str(data.get("name") or "").strip()
        language = str(data.get("language") or "").strip()
        content = str(data.get("content") or "")
        now = _now()
        created_at = str(data.get("created_at") or now)
        updated_at = str(data.get("updated_at") or now)
        # language, content y las columnas de binario tienen columna propia —
        # no duplicar en el JSON de meta.
        meta = {
            k: v
            for k, v in data.items()
            if k
            not in (
                "content",
                "language",
                "binary_b64",
                "binary_filename",
                "binary_size",
                "binary_uploaded_at",
                "is_active",
                "deactivated_at",
            )
        }
        meta_json = _compact_resource_data(meta)
        if _db.IS_PG:
            await conn.execute(
                sql("queries/tools:upsert_pg"),
                (
                    tool_id,
                    owner_id,
                    name,
                    language,
                    scope,
                    meta_json,
                    content,
                    created_at,
                    updated_at,
                ),
            )
        else:
            # Ver skill_storage._upsert: upsert explícito para no perder las
            # columnas que este INSERT no nombra (las de fuente oficial).
            await conn.execute(
                sql("queries/tools:upsert_sqlite"),
                (
                    tool_id,
                    owner_id,
                    name,
                    language,
                    scope,
                    meta_json,
                    content,
                    created_at,
                    updated_at,
                ),
            )

    def _row_to_dict(self, row: Any, include_content: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        d["language"] = row["language"]
        if include_content:
            d["content"] = row["content"]
        d["binary_filename"] = row["binary_filename"]
        d["binary_size"] = row["binary_size"]
        d["binary_uploaded_at"] = row["binary_uploaded_at"]
        d.update(
            {
                "id": row["id"],
                "name": row["name"],
                "resource_type": "tool",
                "scope": row["scope"],
                "is_active": bool(row["is_active"]),
                "deactivated_at": row["deactivated_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        owner = row["owner_id"]
        d["owner_id"] = None if owner == _PUBLIC_OWNER else owner
        runtime = TOOL_RUNTIME_BY_VALUE.get(str(d.get("language") or ""), {})
        has_artifact = bool(d.get("binary_filename"))
        has_source = (
            bool(str(d.get("content") or "").strip())
            if include_content
            else bool(row["has_content"])
        )
        d["ready"] = has_artifact if d.get("language") == "cpp" else has_source
        d["implementations"] = [
            {
                "runtime": d.get("language"),
                "kind": runtime.get("kind", "unknown"),
                "source_available": has_source,
                "artifact_available": has_artifact,
                "target_os": d.get("target_os"),
                "target_arch": d.get("target_arch"),
                "sha256": d.get("binary_sha256", ""),
            }
        ]
        return d

    # ── public API ───────────────────────────────────────────────────────────

    async def get(
        self,
        scope: str,
        tool_id: str,
        owner_id: Optional[str] = None,
        *,
        conn: Optional[AsyncConn] = None,
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_migrated()

        async def fetch(target: AsyncConn) -> Optional[Dict[str, Any]]:
            query = (
                "queries/tools:get_scoped_owned"
                if owner_id is not None
                else "queries/tools:get_scoped"
            )
            params: tuple[Any, ...] = (
                (tool_id, scope, owner_id) if owner_id is not None else (tool_id, scope)
            )
            row = await target.fetchone(sql(query), params)
            if not row:
                # try slug variant (calco 1:1 de SkillStorage.get)
                slug_params: tuple[Any, ...] = (
                    (_slug(tool_id), scope, owner_id)
                    if owner_id is not None
                    else (_slug(tool_id), scope)
                )
                row = await target.fetchone(sql(query), slug_params)
            return self._row_to_dict(row) if row else None

        if conn is not None:
            return await fetch(conn)
        async with open_db() as own_conn:
            return await fetch(own_conn)

    async def list_by_ids(self, tool_ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch lightweight Tool definitions in one query, never artifacts."""
        ids = list(dict.fromkeys(str(tool_id) for tool_id in tool_ids if tool_id))
        if not ids:
            return []
        await self._ensure_migrated()
        placeholders = ",".join("?" for _ in ids)
        statement = sql("queries/tools:list_by_ids").replace("@tool_ids@", placeholders)
        async with open_db() as conn:
            rows = await conn.fetchall(statement, tuple(ids))
        return [self._row_to_dict(row, include_content=False) for row in rows]

    async def save(
        self,
        scope: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
        *,
        conn: Optional[AsyncConn] = None,
        assume_new: bool = False,
    ) -> Dict[str, Any]:
        if scope not in ("private", "public"):
            raise ValueError("scope must be private or public")
        if scope == "public" and not owner_id:
            raise ValueError("Las tools públicas de sistema son de solo lectura")
        await self._ensure_migrated()

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        language = str(payload.get("language") or "").strip()
        if language not in TOOL_RUNTIMES:
            raise ValueError("invalid tool language")
        tool_id = payload.get("id") or generate_id()
        actual_owner = owner_id or "admin"
        now = _now()
        existing = (
            None if assume_new else await self.get_any(tool_id, owner_id=actual_owner)
        )
        if "labels" in payload:
            labels = [str(label) for label in (payload.get("labels") or []) if label]
        elif existing:
            labels = [str(label) for label in (existing.get("labels") or []) if label]
            if existing.get("scope") != scope:
                labels = [
                    label for label in labels if label not in ("private", "public")
                ]
                labels.append(scope)
        else:
            labels = [scope]
        invalid_labels = [label for label in labels if label not in SKILL_LABELS]
        if invalid_labels:
            raise ValueError("invalid tool labels")
        labels = ensure_origin_label(labels)
        # Preserve C++ source imported from official repositories. A source
        # file is not a compiled artifact and must never be discarded merely
        # because the legacy wire value is ``cpp``.
        content = (
            str(payload.get("content") or "").strip()
            if "content" in payload
            else str((existing or {}).get("content") or "")
        )
        # El UPSERT de metadatos no toca las columnas binarias. Conservamos
        # solo sus metadatos ligeros para la respuesta y el JSON compacto.
        next_target_os = (
            str(payload.get("target_os") or "").strip() or None
            if "target_os" in payload
            else (existing or {}).get("target_os")
        )
        next_target_arch = (
            str(payload.get("target_arch") or "").strip() or None
            if "target_arch" in payload
            else (existing or {}).get("target_arch")
        )
        implementation_changed = bool(
            existing
            and (
                ("content" in payload and content != str(existing.get("content") or ""))
                or next_target_os != existing.get("target_os")
                or next_target_arch != existing.get("target_arch")
            )
        )
        preserve_binary = (
            language == "cpp" and existing is not None and not implementation_changed
        )
        binary_filename = existing.get("binary_filename") if preserve_binary else None
        binary_size = existing.get("binary_size") if preserve_binary else None
        binary_uploaded_at = (
            existing.get("binary_uploaded_at") if preserve_binary else None
        )
        binary_sha256 = existing.get("binary_sha256") if preserve_binary else None
        binary_uploaded_by = (
            existing.get("binary_uploaded_by") if preserve_binary else None
        )
        clear_binary = bool(
            existing
            and existing.get("language") == "cpp"
            and (language != "cpp" or implementation_changed)
        )
        data: Dict[str, Any] = {
            "id": tool_id,
            "name": name,
            "resource_type": "tool",
            "description": str(payload.get("description") or "").strip(),
            "instructions": (
                str(payload.get("instructions") or "").strip()
                if "instructions" in payload
                else str((existing or {}).get("instructions") or "")
            ),
            "input_schema": dict(
                payload.get("input_schema")
                if "input_schema" in payload
                else (existing or {}).get("input_schema") or {}
            ),
            "output_schema": dict(
                payload.get("output_schema")
                if "output_schema" in payload
                else (existing or {}).get("output_schema") or {}
            ),
            "target_os": next_target_os,
            "target_arch": next_target_arch,
            "icon": str(payload.get("icon") or "🛠️").strip(),
            "language": language,
            "content": content,
            "binary_filename": binary_filename,
            "binary_size": binary_size,
            "binary_uploaded_at": binary_uploaded_at,
            "labels": labels,
            "scope": scope,
            "owner_id": actual_owner,
            "is_active": existing.get("is_active", True) if existing else True,
            "deactivated_at": existing.get("deactivated_at") if existing else None,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        if binary_sha256:
            data["binary_sha256"] = binary_sha256
        if binary_uploaded_by:
            data["binary_uploaded_by"] = binary_uploaded_by
        if conn is not None:
            await self._upsert(conn, tool_id, actual_owner, scope, data)
            if clear_binary:
                await self.clear_binary(tool_id, actual_owner, conn=conn)
            await self.sync_labels(
                tool_id, actual_owner, data.get("labels") or [], conn=conn
            )
        else:
            async with open_db() as own_conn:
                await self._upsert(own_conn, tool_id, actual_owner, scope, data)
                if clear_binary:
                    await self.clear_binary(tool_id, actual_owner, conn=own_conn)
                await own_conn.commit()
            await self.sync_labels(tool_id, actual_owner, data.get("labels") or [])
        return data

    async def delete(
        self,
        scope: str,
        tool_id: str,
        owner_id: Optional[str] = None,
        allow_public: bool = False,
    ) -> bool:
        if scope == "public" and owner_id is None and not allow_public:
            raise ValueError("Las tools públicas de sistema son de solo lectura")
        await self._ensure_migrated()

        async with open_db() as conn:
            if owner_id is not None:
                row = await conn.fetchone(
                    sql("queries/tools:exists_scoped_owned"),
                    (tool_id, scope, owner_id),
                )
                if not row:
                    return False
                await self.clear_binary(tool_id, str(row["owner_id"]), conn=conn)
                await conn.execute(
                    sql("queries/tools:delete_scoped_owned"),
                    (tool_id, scope, owner_id),
                )
            else:
                row = await conn.fetchone(
                    sql("queries/tools:exists_scoped"),
                    (tool_id, scope),
                )
                if not row:
                    return False
                await self.clear_binary(tool_id, str(row["owner_id"]), conn=conn)
                await conn.execute(sql("queries/tools:delete_scoped"), (tool_id, scope))
            await conn.commit()
        await self.clear_labels(tool_id)
        return True

    # ── binary helpers (usados por rutas, social.py, resource_linking.py) ────

    async def get_binary(
        self, scope: str, tool_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Return deduplicated artifact bytes, with legacy base64 fallback."""
        await self._ensure_migrated()

        async with open_db() as conn:
            artifact_query = (
                "queries/tools:get_artifact"
                if owner_id is None
                else "queries/tools:get_artifact_owned"
            )
            artifact_params: tuple[Any, ...] = (
                (tool_id, scope) if owner_id is None else (tool_id, scope, owner_id)
            )
            row = await conn.fetchone(sql(artifact_query), artifact_params)
            if row:
                metadata = json.loads(row["data"])
                return {
                    "binary_data": bytes(row["binary_data"]),
                    "binary_filename": row["binary_filename"],
                    "binary_size": row["size"],
                    "binary_uploaded_at": row["binary_uploaded_at"],
                    "binary_sha256": row["sha256"],
                    "binary_uploaded_by": metadata.get("binary_uploaded_by"),
                }
            legacy_query = (
                "queries/tools:get_binary"
                if owner_id is None
                else "queries/tools:get_binary_owned"
            )
            legacy_params: tuple[Any, ...] = (
                (tool_id, scope) if owner_id is None else (tool_id, scope, owner_id)
            )
            row = await conn.fetchone(sql(legacy_query), legacy_params)
        if not row or not row["binary_b64"]:
            return None
        metadata = json.loads(row["data"])
        return {
            "binary_data": base64.b64decode(row["binary_b64"], validate=True),
            "binary_filename": row["binary_filename"],
            "binary_size": row["binary_size"],
            "binary_uploaded_at": row["binary_uploaded_at"],
            "binary_sha256": metadata.get("binary_sha256"),
            "binary_uploaded_by": metadata.get("binary_uploaded_by"),
        }

    async def save_binary(
        self,
        tool_id: str,
        owner_id: Optional[str],
        binary_data: str | bytes | bytearray,
        filename: str,
        size: int,
        *,
        sha256: str,
        uploaded_by: str,
        add_review: bool = True,
        conn: Optional[AsyncConn] = None,
    ) -> bool:
        """Store one content-addressed DB artifact and link the Tool to it."""
        await self._ensure_migrated()
        now = _now()
        raw = (
            base64.b64decode(binary_data, validate=True)
            if isinstance(binary_data, str)
            else binary_data
        )

        async def write(target: AsyncConn) -> tuple[bool, str, list[str]]:
            if owner_id is not None:
                row = await target.fetchone(
                    sql("queries/tools:binary_target_owned"),
                    (tool_id, owner_id),
                )
            else:
                row = await target.fetchone(
                    sql("queries/tools:binary_target"), (tool_id,)
                )
            if not row:
                return False, "", []

            actual_sha256 = hashlib.sha256(raw).hexdigest()
            if sha256 and sha256.lower() != actual_sha256:
                raise ValueError("binary sha256 mismatch")
            actual_size = len(raw)

            metadata = json.loads(row["data"])
            labels = [str(label) for label in (metadata.get("labels") or []) if label]
            if add_review:
                labels = list(dict.fromkeys(labels + ["review"]))
            metadata["labels"] = labels
            metadata["binary_sha256"] = actual_sha256
            metadata["binary_uploaded_by"] = uploaded_by
            metadata_json = _compact_resource_data(metadata)
            actual_owner = str(row["owner_id"])

            artifact_query = (
                "queries/tools:insert_artifact_pg"
                if _db.IS_PG
                else "queries/tools:insert_artifact_sqlite"
            )
            link_query = (
                "queries/tools:link_artifact_pg"
                if _db.IS_PG
                else "queries/tools:link_artifact_sqlite"
            )
            await target.execute(
                sql(artifact_query), (actual_sha256, raw, actual_size, now)
            )
            await target.execute(
                sql(link_query), (tool_id, actual_owner, actual_sha256)
            )

            if owner_id is not None:
                await target.execute(
                    sql("queries/tools:set_binary_metadata_owned"),
                    (
                        filename,
                        actual_size,
                        now,
                        metadata_json,
                        now,
                        tool_id,
                        owner_id,
                    ),
                )
            else:
                await target.execute(
                    sql("queries/tools:set_binary_metadata"),
                    (filename, actual_size, now, metadata_json, now, tool_id),
                )
            return True, actual_owner, labels

        if conn is not None:
            saved, actual_owner, labels = await write(conn)
            if saved:
                await self.sync_labels(tool_id, actual_owner, labels, conn=conn)
            return saved
        async with open_db() as own_conn:
            async with own_conn.transaction(immediate=True):
                saved, actual_owner, labels = await write(own_conn)
                if saved:
                    await self.sync_labels(tool_id, actual_owner, labels, conn=own_conn)
        return saved

    async def copy_binary(
        self,
        source_scope: str,
        source_id: str,
        target_id: str,
        target_owner_id: Optional[str],
        *,
        conn: Optional[AsyncConn] = None,
    ) -> bool:
        """Copia explícitamente el binario entre Tools sin cargarlo en get()."""
        binary = await self.get_binary(source_scope, source_id)
        if not binary:
            return False
        return await self.save_binary(
            target_id,
            target_owner_id,
            bytes(binary["binary_data"]),
            str(binary.get("binary_filename") or "tool_binary"),
            int(binary.get("binary_size") or 0),
            sha256=str(binary.get("binary_sha256") or ""),
            uploaded_by=str(binary.get("binary_uploaded_by") or ""),
            add_review=False,
            conn=conn,
        )

    async def restore_version_artifact(
        self,
        tool_id: str,
        owner_id: str,
        version_id: str,
        snapshot: Dict[str, Any],
        *,
        conn: Optional[AsyncConn] = None,
    ) -> bool:
        """Point a Tool at the immutable artifact retained by one version."""
        expected_sha = str(snapshot.get("binary_sha256") or "")
        if not expected_sha:
            return await self.clear_binary(tool_id, owner_id, conn=conn)

        await self._ensure_migrated()

        async def restore(target: AsyncConn) -> bool:
            artifact = await target.fetchone(
                sql("queries/tools:get_version_artifact"), (version_id,)
            )
            if not artifact or str(artifact["sha256"]) != expected_sha:
                return False
            row = await target.fetchone(
                sql("queries/tools:binary_target_owned"), (tool_id, owner_id)
            )
            if not row:
                return False
            metadata = json.loads(row["data"])
            metadata["binary_sha256"] = expected_sha
            uploaded_by = str(snapshot.get("binary_uploaded_by") or "")
            if uploaded_by:
                metadata["binary_uploaded_by"] = uploaded_by
            else:
                metadata.pop("binary_uploaded_by", None)
            link_query = (
                "queries/tools:link_artifact_pg"
                if _db.IS_PG
                else "queries/tools:link_artifact_sqlite"
            )
            await target.execute(sql(link_query), (tool_id, owner_id, expected_sha))
            await target.execute(
                sql("queries/tools:set_binary_metadata_owned"),
                (
                    str(snapshot.get("binary_filename") or "tool_binary"),
                    int(snapshot.get("binary_size") or artifact["size"]),
                    str(snapshot.get("binary_uploaded_at") or ""),
                    _compact_resource_data(metadata),
                    _now(),
                    tool_id,
                    owner_id,
                ),
            )
            return True

        if conn is not None:
            return await restore(conn)
        async with open_db() as own_conn:
            async with own_conn.transaction(immediate=True):
                return await restore(own_conn)

    async def clear_binary(
        self,
        tool_id: str,
        owner_id: Optional[str],
        *,
        conn: Optional[AsyncConn] = None,
    ) -> bool:
        """Elimina ejecutable e integridad sin tocar el resto de la Tool."""
        await self._ensure_migrated()

        async def clear(db_conn: AsyncConn) -> bool:
            if owner_id is not None:
                row = await db_conn.fetchone(
                    sql("queries/tools:binary_target_owned"),
                    (tool_id, owner_id),
                )
            else:
                row = await db_conn.fetchone(
                    sql("queries/tools:binary_target"), (tool_id,)
                )
            if not row:
                return False
            metadata = json.loads(row["data"])
            metadata.pop("binary_sha256", None)
            metadata.pop("binary_uploaded_by", None)
            params = (_compact_resource_data(metadata), _now(), tool_id)
            if owner_id is not None:
                await db_conn.execute(
                    sql("queries/tools:unlink_artifact_owned"),
                    (tool_id, owner_id),
                )
                await db_conn.execute(
                    sql("queries/tools:clear_binary_owned"),
                    (*params, owner_id),
                )
            else:
                await db_conn.execute(sql("queries/tools:unlink_artifact"), (tool_id,))
                await db_conn.execute(sql("queries/tools:clear_binary"), params)
            await db_conn.execute(sql("queries/tools:delete_orphan_artifacts"))
            return True

        if conn is not None:
            return await clear(conn)
        async with open_db() as own_conn:
            cleared = await clear(own_conn)
            await own_conn.commit()
        return cleared
