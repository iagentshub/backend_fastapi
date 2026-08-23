"""Storage de tools: scripts python/shell o binarios cpp precompilados.

Fase 1: solo catalogación y asignación a un agente, sin motor de
ejecución (ver plan de la Fase 2).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.pagination.models import OffsetPage, OffsetParams
from app.sql import sql

# db se importa DOS veces a propósito: ver app/storage/_storage_helpers.py.
from app.storage import db as _db
from app.storage._storage_helpers import _PUBLIC_OWNER, _slug
from app.storage.db import AsyncConn, open_db
from app.storage.db_migrations import _compact_resource_data
from app.storage.resource_base import ResourceStorage
from app.storage.scoped_resource_page import (
    ScopedResourcePageSpec,
    list_scoped_resource_page,
)

# Catálogo de labels compartido; vive en skill_storage (ver comentario allí).
from app.storage.skill_storage import SKILL_LABELS, ensure_origin_label
from app.utils import now_iso as _now
from app.utils.generators import generate_id

# Lenguajes admitidos por Tool. Sin ".bat": producción es Linux, sin capa de
# compatibilidad Windows.
TOOL_LANGUAGES = frozenset({"python", "shell", "cpp"})


class ToolStorage(ResourceStorage):
    """Async DB-backed tool storage (SQLite / PostgreSQL).

    Sin migración legacy de ficheros (a diferencia de Skill): se instancia
    sin argumentos, igual que PromptStorage.
    """

    table = "tools"
    resource_type = "tool"

    async def list_visible_page(
        self,
        *,
        user: str,
        active_group_id: str,
        scope: str,
        page: OffsetParams,
        requested_group_id: str | None = None,
    ) -> OffsetPage[Dict[str, Any]]:
        await self._ensure_migrated()
        spec = ScopedResourcePageSpec(
            table=self.table,
            columns=(
                "resource_row.id, resource_row.owner_id, resource_row.name, "
                "resource_row.language, resource_row.scope, resource_row.data, "
                "resource_row.binary_filename, resource_row.binary_size, "
                "resource_row.binary_uploaded_at, resource_row.created_at, "
                "resource_row.updated_at, resource_row.is_active, "
                "resource_row.deactivated_at"
            ),
            resource_type=self.resource_type,
            decode=lambda row: self._row_to_dict(row, include_content=False),
        )
        return await list_scoped_resource_page(
            spec,
            user=user,
            active_group_id=active_group_id,
            scope=scope,
            include_inactive=None,
            page=page,
            requested_group_id=requested_group_id,
        )

    # ── internal helpers ─────────────────────────────────────────────────────

    async def _upsert(
        self, conn: Any, tool_id: str, owner_id: str, scope: str, data: Dict[str, Any]
    ) -> None:

        name = str(data.get("name") or "").strip()
        language = str(data.get("language") or "").strip()
        content = str(data.get("content") or "")
        binary_b64 = data.get("binary_b64")
        binary_filename = data.get("binary_filename")
        binary_size = data.get("binary_size")
        binary_uploaded_at = data.get("binary_uploaded_at")
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
                    binary_b64,
                    binary_filename,
                    binary_size,
                    binary_uploaded_at,
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
                    binary_b64,
                    binary_filename,
                    binary_size,
                    binary_uploaded_at,
                    created_at,
                    updated_at,
                ),
            )

    def _row_to_dict(self, row: Any, include_content: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        d["language"] = row["language"]
        if include_content:
            d["content"] = row["content"]
            d["binary_b64"] = row["binary_b64"]
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
        return d

    # ── public API ───────────────────────────────────────────────────────────

    async def list(
        self, scope: str = "all", owner_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            if scope == "public":
                rows = await conn.fetchall(sql("queries/tools:list_public"))
            elif scope == "private":
                if owner_id:
                    rows = await conn.fetchall(
                        sql("queries/tools:list_private_by_owner"),
                        (owner_id,),
                    )
                else:
                    rows = await conn.fetchall(sql("queries/tools:list_private"))
            else:  # all
                rows = await conn.fetchall(sql("queries/tools:list_all"))
        return [self._row_to_dict(r, include_content=False) for r in rows]

    async def get(
        self, scope: str, tool_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            owner_filter = " AND owner_id=?" if owner_id is not None else ""
            params: tuple[Any, ...] = (
                (tool_id, scope, owner_id) if owner_id is not None else (tool_id, scope)
            )
            row = await conn.fetchone(
                "SELECT id, owner_id, name, language, scope, data, content, binary_b64, "
                "binary_filename, binary_size, binary_uploaded_at, "
                "is_active, deactivated_at, created_at, updated_at "
                f"FROM tools WHERE id=? AND scope=?{owner_filter} LIMIT 1",
                params,
            )
            if not row:
                # try slug variant (calco 1:1 de SkillStorage.get)
                slug_params: tuple[Any, ...] = (
                    (_slug(tool_id), scope, owner_id)
                    if owner_id is not None
                    else (_slug(tool_id), scope)
                )
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, language, scope, data, content, binary_b64, "
                    "binary_filename, binary_size, binary_uploaded_at, "
                    "is_active, deactivated_at, created_at, updated_at "
                    f"FROM tools WHERE id=? AND scope=?{owner_filter} LIMIT 1",
                    slug_params,
                )
        return self._row_to_dict(row) if row else None

    async def get_any(
        self, tool_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch a tool from any scope — public first, then private."""
        for scope in ("public", "private"):
            result = await self.get(scope, tool_id, owner_id=owner_id)
            if result:
                return result
        return None

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
        if language not in TOOL_LANGUAGES:
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
        # El contenido de una tool cpp vive solo en el binario.
        content = "" if language == "cpp" else str(payload.get("content") or "").strip()
        # Preservar los campos de binario a través de ediciones que solo tocan
        # texto (nombre/descripción/labels): sin esto, un upsert sin estos
        # campos en el payload resetearía el binario ya subido a NULL.
        binary_b64 = (
            payload["binary_b64"]
            if "binary_b64" in payload
            else (existing.get("binary_b64") if existing else None)
        )
        binary_filename = (
            payload["binary_filename"]
            if "binary_filename" in payload
            else (existing.get("binary_filename") if existing else None)
        )
        binary_size = (
            payload["binary_size"]
            if "binary_size" in payload
            else (existing.get("binary_size") if existing else None)
        )
        binary_uploaded_at = (
            payload["binary_uploaded_at"]
            if "binary_uploaded_at" in payload
            else (existing.get("binary_uploaded_at") if existing else None)
        )
        data: Dict[str, Any] = {
            "id": tool_id,
            "name": name,
            "resource_type": "tool",
            "description": str(payload.get("description") or "").strip(),
            "icon": str(payload.get("icon") or "🛠️").strip(),
            "language": language,
            "content": content,
            "binary_b64": binary_b64,
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
        if conn is not None:
            await self._upsert(conn, tool_id, actual_owner, scope, data)
            await self.sync_labels(
                tool_id, actual_owner, data.get("labels") or [], conn=conn
            )
        else:
            async with open_db() as own_conn:
                await self._upsert(own_conn, tool_id, actual_owner, scope, data)
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
                await conn.execute(sql("queries/tools:delete_scoped"), (tool_id, scope))
            await conn.commit()
        await self.clear_labels(tool_id)
        return True

    # ── binary helpers (usados por rutas, social.py, resource_linking.py) ────

    async def get_binary(
        self, scope: str, tool_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Devuelve el binario (base64 + metadatos) de una tool, o None si no
        existe la tool o no tiene binario subido."""
        await self._ensure_migrated()

        async with open_db() as conn:
            owner_filter = " AND owner_id=?" if owner_id is not None else ""
            params: tuple[Any, ...] = (
                (tool_id, scope, owner_id) if owner_id is not None else (tool_id, scope)
            )
            row = await conn.fetchone(
                "SELECT binary_b64, binary_filename, binary_size, binary_uploaded_at "
                f"FROM tools WHERE id=? AND scope=?{owner_filter} LIMIT 1",
                params,
            )
        if not row or not row["binary_b64"]:
            return None
        return {
            "binary_b64": row["binary_b64"],
            "binary_filename": row["binary_filename"],
            "binary_size": row["binary_size"],
            "binary_uploaded_at": row["binary_uploaded_at"],
        }

    async def save_binary(
        self,
        tool_id: str,
        owner_id: Optional[str],
        binary_b64: str,
        filename: str,
        size: int,
    ) -> bool:
        """Guarda el binario subido para una tool cpp existente. Calcado del
        `UPDATE users SET avatar=? WHERE id=?` de auth.py::upload_avatar,
        encapsulado en el storage. owner_id=None → sin filtro de propietario
        (uso admin sobre una tool pública de sistema sin owner). Devuelve
        False si la tool no existe."""
        await self._ensure_migrated()
        now = _now()

        async with open_db() as conn:
            if owner_id is not None:
                row = await conn.fetchone(
                    sql("queries/tools:exists_owned"),
                    (tool_id, owner_id),
                )
                if not row:
                    return False
                await conn.execute(
                    sql("queries/tools:set_binary_owned"),
                    (binary_b64, filename, size, now, now, tool_id, owner_id),
                )
            else:
                row = await conn.fetchone(sql("queries/tools:exists_any"), (tool_id,))
                if not row:
                    return False
                await conn.execute(
                    sql("queries/tools:set_binary"),
                    (binary_b64, filename, size, now, now, tool_id),
                )
            await conn.commit()
        return True
