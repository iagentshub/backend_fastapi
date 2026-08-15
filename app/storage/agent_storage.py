"""Storage de agentes. owner_id='__public__' para agentes de sistema/públicos."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from app.models.agent import Agent
from app.pagination.models import OffsetPage, OffsetParams
from app.services.resource_visibility import VisibilityFilter

# db se importa DOS veces a propósito: ver app/storage/_storage_helpers.py.
from app.storage import db as _db
from app.storage._storage_helpers import _PUBLIC_OWNER
from app.storage.db import AsyncConn, open_db
from app.storage.db_migrations import _compact_resource_data
from app.storage.resource_base import ResourceStorage
from app.storage.scoped_resource_page import (
    ScopedResourcePageSpec,
    list_scoped_resource_page,
)
from app.storage.skill_storage import ensure_origin_label
from app.utils import flog
from app.utils import now_iso as _now
from app.utils.generators import generate_id


class AgentSummary(TypedDict, total=False):
    id: str
    name: str
    agent_type: str
    description: str
    icon: str
    tags: list
    labels: list
    language: str
    connection_id: Optional[str]
    model: str
    temperature: float
    max_tokens: Optional[int]
    timeout: Optional[int]
    skills: list
    use_memory: bool
    memory_file: Optional[str]
    knowledge: list
    tokens_in: int
    tokens_out: int
    scope: str
    created_at: Optional[str]
    updated_at: Optional[str]
    owner_id: Optional[str]


class AgentStorage(ResourceStorage):
    """Async DB-backed agent storage (SQLite / PostgreSQL)."""

    table = "agents"
    resource_type = "agent"

    async def list_visible_page(
        self,
        *,
        user: str,
        active_group_id: str,
        scope: str,
        include_inactive: bool,
        page: OffsetParams,
        requested_group_id: str | None = None,
        extra_filters: tuple[VisibilityFilter, ...] = (),
    ) -> OffsetPage[Dict[str, Any]]:
        await self._ensure_migrated()
        spec = ScopedResourcePageSpec(
            table=self.table,
            columns=(
                "resource_row.id, resource_row.owner_id, resource_row.name, "
                "resource_row.scope, resource_row.data, resource_row.tokens_in, "
                "resource_row.tokens_out, resource_row.is_active, "
                "resource_row.deactivated_at, resource_row.created_at, "
                "resource_row.updated_at"
            ),
            resource_type=self.resource_type,
            decode=self._row_to_dict,
        )
        return await list_scoped_resource_page(
            spec,
            user=user,
            active_group_id=active_group_id,
            scope=scope,
            include_inactive=include_inactive,
            page=page,
            requested_group_id=requested_group_id,
            extra_filters=extra_filters,
            include_public=False,
        )

    def __init__(self, root_dir: Path) -> None:
        super().__init__()
        self._root_dir = Path(root_dir)  # solo para la migración única desde ficheros

    # ── one-time file→DB migration ───────────────────────────────────────────

    async def _migrate_legacy_data(self) -> None:

        async with open_db() as conn:
            try:
                count = await conn.fetchval("SELECT COUNT(*) FROM agents")
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
                for p in sorted(scope_dir.glob("*/config.json")):
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        data["scope"] = scope
                        agent_id = data.get("id") or p.parent.name
                        owner = (
                            _PUBLIC_OWNER
                            if scope == "public"
                            else (data.get("owner_id") or default_owner)
                        )
                        await self._upsert(conn, agent_id, owner, scope, data)
                    except Exception as exc:
                        flog.warning(f"[agents] Migración fallida {p}: {exc}")
            await conn.commit()

    # ── internal helpers ─────────────────────────────────────────────────────

    async def _upsert(
        self, conn: Any, agent_id: str, owner_id: str, scope: str, data: Dict[str, Any]
    ) -> None:

        name = str(data.get("name") or "").strip()
        data_json = _compact_resource_data(data)
        tokens_in = int(data.get("tokens_in") or 0)
        tokens_out = int(data.get("tokens_out") or 0)
        now = _now()
        created_at = str(data.get("created_at") or now)
        updated_at = str(data.get("updated_at") or now)
        is_active = 1 if data.get("is_active", True) else 0
        deactivated_at = data.get("deactivated_at")
        if _db.IS_PG:
            await conn.execute(
                "INSERT INTO agents (id, owner_id, name, scope, data, tokens_in, tokens_out, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET name=EXCLUDED.name, scope=EXCLUDED.scope, data=EXCLUDED.data, "
                "tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out, "
                "is_active=EXCLUDED.is_active, deactivated_at=EXCLUDED.deactivated_at, "
                "updated_at=EXCLUDED.updated_at",
                (
                    agent_id,
                    owner_id,
                    name,
                    scope,
                    data_json,
                    tokens_in,
                    tokens_out,
                    is_active,
                    deactivated_at,
                    created_at,
                    updated_at,
                ),
            )
        else:
            # Ver skill_storage._upsert: upsert explícito para no perder las
            # columnas que este INSERT no nombra (las de fuente oficial).
            await conn.execute(
                "INSERT INTO agents "
                "(id, owner_id, name, scope, data, tokens_in, tokens_out, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET name=excluded.name, "
                "scope=excluded.scope, data=excluded.data, tokens_in=excluded.tokens_in, "
                "tokens_out=excluded.tokens_out, is_active=excluded.is_active, "
                "deactivated_at=excluded.deactivated_at, updated_at=excluded.updated_at",
                (
                    agent_id,
                    owner_id,
                    name,
                    scope,
                    data_json,
                    tokens_in,
                    tokens_out,
                    is_active,
                    deactivated_at,
                    created_at,
                    updated_at,
                ),
            )

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        d.update(
            {
                "id": row["id"],
                "name": row["name"],
                "resource_type": "agent",
                "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"],
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
                    "SELECT id, owner_id, name, scope, data, tokens_in, tokens_out, "
                    "is_active, deactivated_at, created_at, updated_at "
                    "FROM agents WHERE scope='public' ORDER BY created_at ASC"
                )
            elif scope == "private":
                if owner_id:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, name, scope, data, tokens_in, tokens_out, "
                        "is_active, deactivated_at, created_at, updated_at "
                        "FROM agents WHERE scope='private' AND owner_id=? ORDER BY created_at ASC",
                        (owner_id,),
                    )
                else:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, name, scope, data, tokens_in, tokens_out, "
                        "is_active, deactivated_at, created_at, updated_at "
                        "FROM agents WHERE scope='private' ORDER BY created_at ASC"
                    )
            else:  # all
                rows = await conn.fetchall(
                    "SELECT id, owner_id, name, scope, data, tokens_in, tokens_out, "
                    "is_active, deactivated_at, created_at, updated_at "
                    "FROM agents ORDER BY created_at ASC"
                )
        return [self._row_to_dict(r) for r in rows]

    async def get(
        self, agent_id: str, scope: Optional[str] = None, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            if scope == "public":
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, scope, data, tokens_in, tokens_out, "
                    "is_active, deactivated_at, created_at, updated_at "
                    "FROM agents WHERE id=? AND scope='public' LIMIT 1",
                    (agent_id,),
                )
            elif scope == "private":
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, scope, data, tokens_in, tokens_out, "
                    "is_active, deactivated_at, created_at, updated_at "
                    "FROM agents WHERE id=? AND scope='private' LIMIT 1",
                    (agent_id,),
                )
            else:
                # prefer private, fall back to public
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, scope, data, tokens_in, tokens_out, "
                    "is_active, deactivated_at, created_at, updated_at "
                    "FROM agents WHERE id=? ORDER BY CASE scope WHEN 'private' THEN 0 ELSE 1 END LIMIT 1",
                    (agent_id,),
                )
        return self._row_to_dict(row) if row else None

    async def save(
        self,
        payload: Dict[str, Any],
        scope: str = "private",
        owner_id: Optional[str] = None,
        *,
        conn: Optional[AsyncConn] = None,
        assume_new: bool = False,
    ) -> Dict[str, Any]:
        await self._ensure_migrated()

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        agent_id = payload.get("id") or generate_id()
        actual_owner = owner_id or "admin"
        existing = None if assume_new else await self.get(agent_id, scope="private")
        now = _now()
        raw_labels = [
            str(label) for label in (payload.get("labels") or [scope]) if label
        ]
        agent = Agent.from_dict(
            {
                **payload,
                "labels": ensure_origin_label(raw_labels),
                "id": agent_id,
                "scope": scope,
                "owner_id": actual_owner,
                "created_at": existing.get("created_at", now) if existing else now,
                "updated_at": now,
                # Conservar el estado de borrado suave a través de las ediciones:
                # editar un agente desactivado no debe reactivarlo.
                "is_active": existing.get("is_active", True) if existing else True,
                "deactivated_at": existing.get("deactivated_at") if existing else None,
            }
        )
        data = agent.to_dict()
        if conn is not None:
            await self._upsert(conn, agent_id, actual_owner, scope, data)
            await self.sync_labels(
                agent_id, actual_owner, data.get("labels") or [], conn=conn
            )
        else:
            async with open_db() as own_conn:
                await self._upsert(own_conn, agent_id, actual_owner, scope, data)
                await own_conn.commit()
            await self.sync_labels(agent_id, actual_owner, data.get("labels") or [])
        return data

    async def add_tokens(
        self,
        agent_id: str,
        tokens_in: int,
        tokens_out: int,
        owner_id: Optional[str] = None,
    ) -> None:

        async with open_db() as conn:
            if owner_id is not None:
                await conn.execute(
                    "UPDATE agents SET tokens_in=tokens_in+?, tokens_out=tokens_out+? "
                    "WHERE id=? AND scope='private' AND owner_id=?",
                    (tokens_in, tokens_out, agent_id, owner_id),
                )
            else:
                await conn.execute(
                    "UPDATE agents SET tokens_in=tokens_in+?, tokens_out=tokens_out+? "
                    "WHERE id=? AND scope='private'",
                    (tokens_in, tokens_out, agent_id),
                )
            await conn.commit()

    async def delete(
        self,
        agent_id: str,
        scope: Optional[str] = None,
        owner_id: Optional[str] = None,
        allow_public: bool = False,
    ) -> bool:
        await self._ensure_migrated()
        if scope == "public" and not allow_public:
            raise ValueError("Los agentes públicos son de solo lectura")

        async with open_db() as conn:
            if allow_public:
                row = await conn.fetchone(
                    "SELECT id FROM agents WHERE id=? LIMIT 1", (agent_id,)
                )
                if not row:
                    return False
                await conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
            elif owner_id is not None:
                row = await conn.fetchone(
                    "SELECT id FROM agents WHERE id=? AND scope!='public' AND owner_id=? LIMIT 1",
                    (agent_id, owner_id),
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM agents WHERE id=? AND scope!='public' AND owner_id=?",
                    (agent_id, owner_id),
                )
            else:
                row = await conn.fetchone(
                    "SELECT id FROM agents WHERE id=? AND scope!='public' LIMIT 1",
                    (agent_id,),
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM agents WHERE id=? AND scope!='public'",
                    (agent_id,),
                )
            await conn.commit()
        await self.clear_labels(agent_id)
        return True

    def _summary(self, a: Dict[str, Any]) -> AgentSummary:
        scope = a.get("scope", "private")
        return {
            "id": a["id"],
            "name": a.get("name", a["id"]),
            "agent_type": a.get("agent_type", "generic"),
            "description": a.get("description", ""),
            "icon": a.get("icon", ""),
            "tags": a.get("tags", []),
            "labels": a.get("labels")
            or (["public"] if scope == "public" else ["private"]),
            "language": a.get("language", ""),
            "connection_id": a.get("connection_id"),
            "model": a.get("model", ""),
            "temperature": a.get("temperature", 0.7),
            "max_tokens": a.get("max_tokens"),
            "timeout": a.get("timeout"),
            "skills": a.get("skills", []),
            "use_memory": a.get("use_memory", False),
            "memory_file": a.get("memory_file"),
            "knowledge": a.get("knowledge", []),
            "tokens_in": int(a.get("tokens_in") or 0),
            "tokens_out": int(a.get("tokens_out") or 0),
            "scope": scope,
            "created_at": a.get("created_at"),
            "updated_at": a.get("updated_at"),
            "owner_id": a.get("owner_id"),
        }
