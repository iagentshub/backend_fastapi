"""Storage de skills. owner_id='__public__' para skills de sistema/públicas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.config.content_languages import CONTENT_LANGUAGE_LABELS
from app.sql import sql

# db se importa DOS veces a propósito: ver app/storage/_storage_helpers.py.
from app.storage import db as _db
from app.storage._storage_helpers import _PUBLIC_OWNER, _slug
from app.storage.db import DB_ERRORS, AsyncConn, open_db
from app.storage.db_migrations import _compact_resource_data
from app.storage.resource_base import ResourceStorage
from app.storage.scoped_resource_page import ScopedResourcePageSpec
from app.storage.scoped_resource_pagination import ScopedResourcePaginationMixin
from app.utils import flog
from app.utils import now_iso as _now
from app.utils.generators import generate_id

SKILL_CATEGORIES = frozenset(
    {
        "ai",
        "messaging",
        "notes",
        "productivity",
        "dev",
        "security",
        "media",
        "data",
        "company",
    }
)

# Labels are selected from the shared system catalog. ``linked``/``fork`` and
# the origin labels are system-managed, not user tags.
#
# El catálogo es compartido a propósito: prompt_storage y tool_storage validan
# contra esta misma lista y la importan desde aquí. Vive en el módulo de skill
# porque es donde nació y de donde lo importan ya los routers; no lo dupliques.
SKILL_LABELS = (
    frozenset(
        {
            "private",
            "public",
            "production",
            "staging",
            "development",
            "test",
            "favorite",
            "draft",
            "review",
            "deprecated",
            "quarantine",
            "archived",
            "delete",
            "linked",
            "fork",
            "official",
            "community",
        }
    )
    | CONTENT_LANGUAGE_LABELS
)
ORIGIN_LABELS = frozenset({"official", "community"})
SKILL_ASSIGNABLE_LABELS = SKILL_LABELS - {"linked", "fork"} - ORIGIN_LABELS


def ensure_origin_label(labels: List[str], origin: Optional[str] = None) -> List[str]:
    """Return one system origin label; legacy resources default to community."""
    chosen = origin
    if chosen not in ORIGIN_LABELS:
        chosen = "official" if "official" in labels else "community"
    normalized = list(
        dict.fromkeys(label for label in labels if label not in ORIGIN_LABELS)
    )
    insert_at = next(
        (
            index + 1
            for index, label in enumerate(normalized)
            if label in {"private", "public"}
        ),
        0,
    )
    normalized.insert(insert_at, chosen)
    return normalized


def _parse_skill_md(raw: str, default_id: str = "") -> Dict[str, Any]:
    """Parsea SKILL.md con frontmatter YAML."""
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        meta = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip() if len(parts) > 2 else ""
    else:
        meta = {}
        body = raw.strip()
    meta["content"] = body
    meta.setdefault("id", default_id)
    meta.setdefault("name", default_id)
    return meta


class SkillStorage(ScopedResourcePaginationMixin, ResourceStorage):
    """Async DB-backed skill storage (SQLite / PostgreSQL)."""

    table = "skills"
    resource_type = "skill"

    def _page_spec(self) -> ScopedResourcePageSpec:
        return ScopedResourcePageSpec(
            table=self.table,
            columns=(
                "resource_row.id, resource_row.owner_id, resource_row.name, "
                "resource_row.category, resource_row.scope, resource_row.data, "
                "resource_row.is_active, resource_row.deactivated_at, "
                "resource_row.created_at, resource_row.updated_at"
            ),
            resource_type=self.resource_type,
            decode=lambda row: self._row_to_dict(row, include_content=False),
        )

    def __init__(self, root_dir: Path) -> None:
        super().__init__()
        self._root_dir = Path(root_dir)  # solo para la migración única desde ficheros

    # ── one-time file→DB migration ───────────────────────────────────────────

    async def _migrate_legacy_data(self) -> None:

        async with open_db() as conn:
            try:
                count = await conn.fetchval(sql("queries/skills:count_all"))
                if count:
                    return
            except DB_ERRORS as exc:
                # Tabla aún inexistente (arranque previo a la migración de
                # esquema) o BD caída. Ver agent_storage para el razonamiento.
                flog.debug(f"[skills] Migración legacy omitida: {exc}")
                return
            for scope, default_owner in [
                ("public", _PUBLIC_OWNER),
                ("private", "admin"),
            ]:
                scope_dir = self._root_dir / scope
                if not scope_dir.exists():
                    continue
                for p in sorted(scope_dir.glob("*/SKILL.md")):
                    try:
                        meta = _parse_skill_md(
                            p.read_text(encoding="utf-8"), default_id=p.parent.name
                        )
                        meta["scope"] = scope
                        skill_id = meta.get("id") or p.parent.name
                        owner = (
                            _PUBLIC_OWNER
                            if scope == "public"
                            else (meta.get("owner_id") or default_owner)
                        )
                        await self._upsert(conn, skill_id, owner, scope, meta)
                    except Exception as exc:  # noqa: BLE001
                        # Ancho a propósito: un YAML corrupto no puede parar la
                        # migración del resto. Ver agent_storage.
                        flog.warning(f"[skills] Migración fallida {p}: {exc}")
            await conn.commit()

    # ── internal helpers ─────────────────────────────────────────────────────

    async def _upsert(
        self, conn: Any, skill_id: str, owner_id: str, scope: str, data: Dict[str, Any]
    ) -> None:

        name = str(data.get("name") or "").strip()
        content = str(data.get("content") or "")
        now = _now()
        created_at = str(data.get("created_at") or now)
        updated_at = str(data.get("updated_at") or now)
        # Skill tags are centrally defined metadata, not user-authored data.
        # Never persist arbitrary tags received from clients or legacy files.
        meta = {
            k: v
            for k, v in data.items()
            if k not in ("content", "tags", "category", "is_active", "deactivated_at")
        }
        meta_json = _compact_resource_data(meta)
        if _db.IS_PG:
            await conn.execute(
                sql("queries/skills:upsert_pg"),
                (
                    skill_id,
                    owner_id,
                    name,
                    data.get("category"),
                    scope,
                    meta_json,
                    content,
                    created_at,
                    updated_at,
                ),
            )
        else:
            # Upsert explícito, no INSERT OR REPLACE: reemplazar la fila entera
            # borraría las columnas que este INSERT no nombra
            # (official_source_id / official_component_id, que gestiona
            # official_source_storage) cada vez que se guarda el recurso.
            await conn.execute(
                sql("queries/skills:upsert_sqlite"),
                (
                    skill_id,
                    owner_id,
                    name,
                    data.get("category"),
                    scope,
                    meta_json,
                    content,
                    created_at,
                    updated_at,
                ),
            )

    def _row_to_dict(self, row: Any, include_content: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        d.pop("tags", None)
        d["category"] = row["category"]
        if include_content:
            d["content"] = row["content"]
        d.update(
            {
                "id": row["id"],
                "name": row["name"],
                "resource_type": "skill",
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
                rows = await conn.fetchall(sql("queries/skills:list_public"))
            elif scope == "private":
                if owner_id:
                    rows = await conn.fetchall(
                        sql("queries/skills:list_private_by_owner"),
                        (owner_id,),
                    )
                else:
                    rows = await conn.fetchall(sql("queries/skills:list_private"))
            else:  # all
                rows = await conn.fetchall(sql("queries/skills:list_all"))
        return [self._row_to_dict(r, include_content=False) for r in rows]

    async def get(
        self, scope: str, skill_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            owner_filter = " AND owner_id=?" if owner_id is not None else ""
            params: tuple[Any, ...] = (
                (skill_id, scope, owner_id)
                if owner_id is not None
                else (skill_id, scope)
            )
            row = await conn.fetchone(
                "SELECT id, owner_id, name, category, scope, data, content, "
                "is_active, deactivated_at, created_at, updated_at "
                f"FROM skills WHERE id=? AND scope=?{owner_filter} LIMIT 1",
                params,
            )
            if not row:
                # try slug variant
                slug_params: tuple[Any, ...] = (
                    (_slug(skill_id), scope, owner_id)
                    if owner_id is not None
                    else (_slug(skill_id), scope)
                )
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, category, scope, data, content, "
                    "is_active, deactivated_at, created_at, updated_at "
                    f"FROM skills WHERE id=? AND scope=?{owner_filter} LIMIT 1",
                    slug_params,
                )
        return self._row_to_dict(row) if row else None

    async def get_any(
        self, skill_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch a skill from any scope — public first, then private."""
        for scope in ("public", "private"):
            result = await self.get(scope, skill_id, owner_id=owner_id)
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
            raise ValueError("Las skills públicas de sistema son de solo lectura")
        await self._ensure_migrated()

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        category = str(payload.get("category") or "").strip()
        if category and category not in SKILL_CATEGORIES:
            raise ValueError("invalid skill category")
        skill_id = payload.get("id") or generate_id()
        actual_owner = owner_id or "admin"
        now = _now()
        existing = (
            None if assume_new else await self.get_any(skill_id, owner_id=actual_owner)
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
            raise ValueError("invalid skill labels")
        labels = ensure_origin_label(labels)
        data: Dict[str, Any] = {
            "id": skill_id,
            "name": name,
            "resource_type": "skill",
            "description": str(payload.get("description") or "").strip(),
            "icon": str(payload.get("icon") or "🔧").strip(),
            "category": category or None,
            "content": str(payload.get("content") or "").strip(),
            "labels": labels,
            "scope": scope,
            "owner_id": actual_owner,
            "is_active": existing.get("is_active", True) if existing else True,
            "deactivated_at": existing.get("deactivated_at") if existing else None,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        if conn is not None:
            await self._upsert(conn, skill_id, actual_owner, scope, data)
            await self.sync_labels(
                skill_id, actual_owner, data.get("labels") or [], conn=conn
            )
        else:
            async with open_db() as own_conn:
                await self._upsert(own_conn, skill_id, actual_owner, scope, data)
                await own_conn.commit()
            await self.sync_labels(skill_id, actual_owner, data.get("labels") or [])
        return data

    async def delete(
        self,
        scope: str,
        skill_id: str,
        owner_id: Optional[str] = None,
        allow_public: bool = False,
    ) -> bool:
        if scope == "public" and owner_id is None and not allow_public:
            raise ValueError("Las skills públicas de sistema son de solo lectura")
        await self._ensure_migrated()

        async with open_db() as conn:
            if owner_id is not None:
                row = await conn.fetchone(
                    sql("queries/skills:exists_scoped_owned"),
                    (skill_id, scope, owner_id),
                )
                if not row:
                    return False
                await conn.execute(
                    sql("queries/skills:delete_scoped_owned"),
                    (skill_id, scope, owner_id),
                )
            else:
                row = await conn.fetchone(
                    sql("queries/skills:exists_scoped"),
                    (skill_id, scope),
                )
                if not row:
                    return False
                await conn.execute(
                    sql("queries/skills:delete_scoped"), (skill_id, scope)
                )
            await conn.commit()
        await self.clear_labels(skill_id)
        return True
