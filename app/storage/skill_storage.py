"""Storage de skills. owner_id='__public__' para skills de sistema/públicas."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# db se importa DOS veces a propósito: ver app/storage/_storage_helpers.py.
from app.storage import db as _db
from app.storage._storage_helpers import _PUBLIC_OWNER, _slug
from app.storage.db import open_db
from app.storage.db_migrations import _compact_resource_data
from app.storage.resource_base import ResourceStorage
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

# Labels are selected from the shared system catalog. ``linked`` and ``fork``
# are internal provenance labels used by linking/copy flows, not user tags.
#
# El catálogo es compartido a propósito: prompt_storage y tool_storage validan
# contra esta misma lista y la importan desde aquí. Vive en el módulo de skill
# porque es donde nació y de donde lo importan ya los routers; no lo dupliques.
SKILL_LABELS = frozenset(
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
    }
)
SKILL_ASSIGNABLE_LABELS = SKILL_LABELS - {"linked", "fork"}


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


class SkillStorage(ResourceStorage):
    """Async DB-backed skill storage (SQLite / PostgreSQL)."""
    table = "skills"
    resource_type = "skill"

    def __init__(self, root_dir: Path) -> None:
        super().__init__()
        self._root_dir = Path(root_dir)  # solo para la migración única desde ficheros

    # ── one-time file→DB migration ───────────────────────────────────────────

    async def _migrate_legacy_data(self) -> None:

        async with open_db() as conn:
            try:
                count = await conn.fetchval("SELECT COUNT(*) FROM skills")
                if count:
                    return
            except Exception:
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
                    except Exception as exc:
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
        is_active = 1 if data.get("is_active", True) else 0
        deactivated_at = data.get("deactivated_at")
        # Skill tags are centrally defined metadata, not user-authored data.
        # Never persist arbitrary tags received from clients or legacy files.
        meta = {
            k: v for k, v in data.items() if k not in ("content", "tags", "category")
        }
        meta_json = _compact_resource_data(meta)
        if _db.IS_PG:
            await conn.execute(
                "INSERT INTO skills (id, owner_id, name, category, scope, data, content, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET name=EXCLUDED.name, category=EXCLUDED.category, scope=EXCLUDED.scope, data=EXCLUDED.data, "
                "content=EXCLUDED.content, is_active=EXCLUDED.is_active, "
                "deactivated_at=EXCLUDED.deactivated_at, updated_at=EXCLUDED.updated_at",
                (
                    skill_id,
                    owner_id,
                    name,
                    data.get("category"),
                    scope,
                    meta_json,
                    content,
                    is_active,
                    deactivated_at,
                    created_at,
                    updated_at,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR REPLACE INTO skills "
                "(id, owner_id, name, category, scope, data, content, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    skill_id,
                    owner_id,
                    name,
                    data.get("category"),
                    scope,
                    meta_json,
                    content,
                    is_active,
                    deactivated_at,
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
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        d["is_active"] = bool(row["is_active"])
        d["deactivated_at"] = row["deactivated_at"]
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
                rows = await conn.fetchall(
                    "SELECT id, owner_id, name, category, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at "
                    "FROM skills WHERE scope='public' ORDER BY created_at ASC"
                )
            elif scope == "private":
                if owner_id:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, name, category, scope, data, content, is_active, "
                        "deactivated_at, created_at, updated_at "
                        "FROM skills WHERE scope='private' AND owner_id=? ORDER BY created_at ASC",
                        (owner_id,),
                    )
                else:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, name, category, scope, data, content, is_active, "
                        "deactivated_at, created_at, updated_at "
                        "FROM skills WHERE scope='private' ORDER BY created_at ASC"
                    )
            else:  # all
                rows = await conn.fetchall(
                    "SELECT id, owner_id, name, category, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at "
                    "FROM skills ORDER BY created_at ASC"
                )
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
                "SELECT id, owner_id, name, category, scope, data, content, is_active, "
                "deactivated_at, created_at, updated_at "
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
                    "SELECT id, owner_id, name, category, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at "
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
        self, scope: str, payload: Dict[str, Any], owner_id: Optional[str] = None
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
        existing = await self.get_any(skill_id, owner_id=actual_owner)
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
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
            # Conservar el borrado suave a través de las ediciones.
            "is_active": existing.get("is_active", True) if existing else True,
            "deactivated_at": existing.get("deactivated_at") if existing else None,
        }
        async with open_db() as conn:
            await self._upsert(conn, skill_id, actual_owner, scope, data)
            await conn.commit()
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
                    "SELECT id FROM skills WHERE id=? AND scope=? AND owner_id=? LIMIT 1",
                    (skill_id, scope, owner_id),
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM skills WHERE id=? AND scope=? AND owner_id=?",
                    (skill_id, scope, owner_id),
                )
            else:
                row = await conn.fetchone(
                    "SELECT id FROM skills WHERE id=? AND scope=? LIMIT 1",
                    (skill_id, scope),
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM skills WHERE id=? AND scope=?", (skill_id, scope)
                )
            await conn.commit()
        await self.clear_labels(skill_id)
        return True
