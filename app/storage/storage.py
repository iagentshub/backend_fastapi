"""Storage: Agentes, Conexiones, Skills y Memoria."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import yaml

from app.models.agent import Agent

# db se importa DOS veces a propósito y no es un descuido:
#
# - open_db es una función; da igual cuándo se resuelva el nombre, y traerla por
#   valor deja el código legible.
# - IS_PG es un BOOLEANO que los tests reescriben con
#   monkeypatch.setattr(db, "IS_PG", False). Traerlo por valor congelaría el
#   del arranque y toda la suite correría contra el dialecto equivocado — la
#   trampa que documenta CLAUDE.md. Leerlo como _db.IS_PG lo consulta en cada
#   llamada, que es lo único correcto aquí.
#
# Los 31 imports de db que había dentro de funciones no rompían ningún ciclo:
# db.py no alcanza storage.py ni directa ni indirectamente. Eran costumbre, y
# escondían los pocos diferidos que sí tienen motivo (ver DATA_DIR más abajo).
from app.storage import db as _db
from app.storage.crypto import decrypt, encrypt
from app.storage.db import open_db
from app.storage.db_migrations import _compact_resource_data
from app.storage.migration import LegacyMigrationStorage
from app.storage.resource_base import ResourceStorage
from app.utils import flog
from app.utils import now_iso as _now
from app.utils.generators import generate_id

# ─── helpers ──────────────────────────────────────────────────────────────────

_PUBLIC_OWNER = "__public__"


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


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s or f"item-{generate_id(8)}"


def _display_name(data: Dict[str, Any], resource_id: str) -> str:
    """Canonical name, with legacy connection fields as compatibility fallbacks."""
    for key in ("name", "label", "type"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return resource_id


# ─── ConnectionStorage ────────────────────────────────────────────────────────


# Campos sensibles que se cifran en la BD
_ENCRYPTED_FIELDS = ("api_key", "password", "ssh_key")


class ConnectionStorage(ResourceStorage):
    """DB-backed async connection storage."""
    table = "connections"
    resource_type = "connection"

    async def _migrate_legacy_data(self) -> None:
        """One-time import from connections.json if table is empty."""
        # Este SÍ tiene que quedarse dentro de la función: DATA_DIR se reescribe
        # en cada test (conftest: patch_data_dir) y subirlo arriba lo congelaría
        # al directorio de la fase de colección.
        from app.config.data import DATA_DIR

        async with open_db() as conn:
            try:
                count = await conn.fetchval("SELECT COUNT(*) FROM connections")
                if count:
                    return
            except Exception:
                return
            old = DATA_DIR / "connections" / "connections.json"
            if not old.exists():
                return
            try:
                items = json.loads(old.read_text(encoding="utf-8"))
                for item in items:
                    await self._upsert(conn, item)
                await conn.commit()
                old.rename(old.with_suffix(".migrated"))
            except Exception as exc:
                flog.warning(f"[storage] Migración de connections.json fallida: {exc}")

    async def _upsert(
        self, conn: Any, payload: Dict[str, Any], owner_id: str = "admin"
    ) -> None:
        """Insert or replace a connection row (uses AsyncConn, ? placeholders)."""
        conn_id = str(payload.get("id") or "").strip() or generate_id()
        payload["id"] = conn_id
        name = _display_name(payload, conn_id)
        is_active = 1 if payload.get("is_active", True) else 0
        deactivated_at = payload.get("deactivated_at")
        data_json = _compact_resource_data(payload)
        if _db.IS_PG:
            await conn.execute(
                "INSERT INTO connections (id, owner_id, name, data, tokens_in, tokens_out, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET owner_id=EXCLUDED.owner_id, name=EXCLUDED.name, data=EXCLUDED.data, "
                "tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out, "
                "is_active=EXCLUDED.is_active, deactivated_at=EXCLUDED.deactivated_at, "
                "updated_at=EXCLUDED.updated_at",
                (
                    conn_id,
                    owner_id,
                    name,
                    data_json,
                    int(payload.get("tokens_in") or 0),
                    int(payload.get("tokens_out") or 0),
                    is_active,
                    deactivated_at,
                    str(payload.get("created_at") or _now()),
                    str(payload.get("updated_at") or _now()),
                ),
            )
        else:
            await conn.execute(
                "INSERT OR REPLACE INTO connections "
                "(id, owner_id, name, data, tokens_in, tokens_out, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conn_id,
                    owner_id,
                    name,
                    data_json,
                    int(payload.get("tokens_in") or 0),
                    int(payload.get("tokens_out") or 0),
                    is_active,
                    deactivated_at,
                    str(payload.get("created_at") or _now()),
                    str(payload.get("updated_at") or _now()),
                ),
            )

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        for field in _ENCRYPTED_FIELDS:
            if d.get(field):
                d[field] = decrypt(d[field])
        d.update(
            {
                "id": row["id"],
                "name": row["name"],
                "resource_type": "connection",
                "scope": "private",
                "owner_id": row["owner_id"],
                "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        d.setdefault("description", "")
        d.setdefault("icon", "")
        d.setdefault("labels", ["private"])
        d["is_active"] = bool(row["is_active"])
        d["deactivated_at"] = row["deactivated_at"]
        return d

    async def list(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """owner_id=None → admin sees all. owner_id=str → own connections only."""
        await self._ensure_migrated()

        async with open_db() as conn:
            if owner_id is None:
                rows = await conn.fetchall(
                    "SELECT id, owner_id, name, data, tokens_in, tokens_out, is_active, "
                    "deactivated_at, created_at, updated_at FROM connections ORDER BY created_at ASC"
                )
            else:
                rows = await conn.fetchall(
                    "SELECT id, owner_id, name, data, tokens_in, tokens_out, is_active, "
                    "deactivated_at, created_at, updated_at FROM connections "
                    "WHERE owner_id = ? ORDER BY created_at ASC",
                    (owner_id,),
                )
        return [self._row_to_dict(r) for r in rows]

    async def get(
        self, conn_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:

        async with open_db() as conn:
            if owner_id is None:
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, data, tokens_in, tokens_out, is_active, "
                    "deactivated_at, created_at, updated_at FROM connections WHERE id = ?",
                    (conn_id,),
                )
            else:
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, data, tokens_in, tokens_out, is_active, "
                    "deactivated_at, created_at, updated_at FROM connections "
                    "WHERE id = ? AND owner_id = ?",
                    (conn_id, owner_id),
                )
        return self._row_to_dict(row) if row else None

    async def save(
        self, payload: Dict[str, Any], owner_id: str = "admin"
    ) -> Dict[str, Any]:

        conn_id = str(payload.get("id") or "").strip() or generate_id()
        payload["id"] = conn_id
        payload["name"] = _display_name(payload, conn_id)
        existing = await self.get(conn_id, owner_id)
        if existing:
            payload["created_at"] = existing.get("created_at", _now())
            # Conservar campos cifrados si no se envían nuevos
            for field in _ENCRYPTED_FIELDS:
                if not payload.get(field) and existing.get(field):
                    payload[field] = existing[field]
            # Conservar el borrado suave a través de las ediciones.
            payload["is_active"] = existing.get("is_active", True)
            payload["deactivated_at"] = existing.get("deactivated_at")
        else:
            payload.setdefault("created_at", _now())
            payload.setdefault("is_active", True)
        payload["updated_at"] = _now()
        payload.update(
            {
                "resource_type": "connection",
                "scope": "private",
                "owner_id": owner_id,
            }
        )
        payload.setdefault("description", "")
        payload.setdefault("icon", "")
        payload.setdefault("labels", ["private"])
        stored = dict(payload)
        for field in _ENCRYPTED_FIELDS:
            if stored.get(field):
                stored[field] = encrypt(stored[field])
        async with open_db() as conn:
            await self._upsert(conn, stored, owner_id)
            await conn.commit()
        await self.sync_labels(conn_id, owner_id, payload.get("labels") or [])
        return payload

    async def delete(self, conn_id: str, owner_id: Optional[str] = None) -> bool:

        async with open_db() as conn:
            if owner_id is None:
                existing = await conn.fetchone(
                    "SELECT id FROM connections WHERE id = ?", (conn_id,)
                )
            else:
                existing = await conn.fetchone(
                    "SELECT id FROM connections WHERE id = ? AND owner_id = ?",
                    (conn_id, owner_id),
                )
            if not existing:
                return False
            if owner_id is None:
                await conn.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
            else:
                await conn.execute(
                    "DELETE FROM connections WHERE id = ? AND owner_id = ?",
                    (conn_id, owner_id),
                )
            await conn.commit()
        await self.clear_labels(conn_id)
        return True

    async def add_tokens(
        self, conn_id: str, input_tokens: int, output_tokens: int
    ) -> None:

        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE connections SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ? WHERE id = ?",
                    (input_tokens, output_tokens, conn_id),
                )
                total = input_tokens + output_tokens
                if total > 0:
                    row = await conn.fetchone(
                        "SELECT owner_id FROM connections WHERE id = ?", (conn_id,)
                    )
                    if row:
                        today = date.today().isoformat()
                        owner = row["owner_id"]
                        if _db.IS_PG:
                            await conn.execute(
                                "INSERT INTO token_daily (day, owner_id, tokens) VALUES (?, ?, ?) "
                                "ON CONFLICT (day, owner_id) DO UPDATE SET tokens = token_daily.tokens + EXCLUDED.tokens",
                                (today, owner, total),
                            )
                        else:
                            await conn.execute(
                                "INSERT INTO token_daily (day, owner_id, tokens) VALUES (?, ?, ?) "
                                "ON CONFLICT(day, owner_id) DO UPDATE SET tokens = token_daily.tokens + excluded.tokens",
                                (today, owner, total),
                            )


# ─── AgentStorage ─────────────────────────────────────────────────────────────
# DB-backed. owner_id='__public__' para agentes de sistema/públicos.


class AgentStorage(ResourceStorage):
    """Async DB-backed agent storage (SQLite / PostgreSQL)."""
    table = "agents"
    resource_type = "agent"

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
            await conn.execute(
                "INSERT OR REPLACE INTO agents "
                "(id, owner_id, name, scope, data, tokens_in, tokens_out, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    ) -> Dict[str, Any]:
        await self._ensure_migrated()

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        agent_id = payload.get("id") or generate_id()
        actual_owner = owner_id or "admin"
        existing = await self.get(agent_id, scope="private")
        now = _now()
        agent = Agent.from_dict(
            {
                **payload,
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
        async with open_db() as conn:
            await self._upsert(conn, agent_id, actual_owner, scope, data)
            await conn.commit()
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


# ─── SkillStorage ─────────────────────────────────────────────────────────────
# DB-backed. owner_id='__public__' para skills de sistema/públicas.

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


# ─── PromptStorage ────────────────────────────────────────────────────────────
# DB-backed. Prompts reutilizables invocables desde el chat vía "@alias".

PROMPT_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]$")


class PromptStorage(ResourceStorage):
    """Async DB-backed prompt storage (SQLite / PostgreSQL)."""
    table = "prompts"
    resource_type = "prompt"

    # ── internal helpers ─────────────────────────────────────────────────────

    async def _upsert(
        self, conn: Any, prompt_id: str, owner_id: str, scope: str, data: Dict[str, Any]
    ) -> None:

        name = str(data.get("name") or "").strip()
        content = str(data.get("content") or "")
        alias = str(data.get("alias") or "").strip().lower()
        now = _now()
        created_at = str(data.get("created_at") or now)
        updated_at = str(data.get("updated_at") or now)
        is_active = 1 if data.get("is_active", True) else 0
        deactivated_at = data.get("deactivated_at")
        # alias y content tienen columna propia — no duplicar en el JSON de meta.
        meta = {k: v for k, v in data.items() if k not in ("content", "alias")}
        meta_json = _compact_resource_data(meta)
        if _db.IS_PG:
            await conn.execute(
                "INSERT INTO prompts (id, owner_id, name, alias, scope, data, content, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET name=EXCLUDED.name, alias=EXCLUDED.alias, scope=EXCLUDED.scope, data=EXCLUDED.data, "
                "content=EXCLUDED.content, is_active=EXCLUDED.is_active, "
                "deactivated_at=EXCLUDED.deactivated_at, updated_at=EXCLUDED.updated_at",
                (
                    prompt_id,
                    owner_id,
                    name,
                    alias,
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
                "INSERT OR REPLACE INTO prompts "
                "(id, owner_id, name, alias, scope, data, content, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    prompt_id,
                    owner_id,
                    name,
                    alias,
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
        d["alias"] = row["alias"]
        if include_content:
            d["content"] = row["content"]
        d.update(
            {
                "id": row["id"],
                "name": row["name"],
                "resource_type": "prompt",
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
                    "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at "
                    "FROM prompts WHERE scope='public' ORDER BY created_at ASC"
                )
            elif scope == "private":
                if owner_id:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                        "deactivated_at, created_at, updated_at "
                        "FROM prompts WHERE scope='private' AND owner_id=? ORDER BY created_at ASC",
                        (owner_id,),
                    )
                else:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                        "deactivated_at, created_at, updated_at "
                        "FROM prompts WHERE scope='private' ORDER BY created_at ASC"
                    )
            else:  # all
                rows = await conn.fetchall(
                    "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at "
                    "FROM prompts ORDER BY created_at ASC"
                )
        return [self._row_to_dict(r, include_content=False) for r in rows]

    async def get(
        self, scope: str, prompt_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            owner_filter = " AND owner_id=?" if owner_id is not None else ""
            params: tuple[Any, ...] = (
                (prompt_id, scope, owner_id)
                if owner_id is not None
                else (prompt_id, scope)
            )
            row = await conn.fetchone(
                "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                "deactivated_at, created_at, updated_at "
                f"FROM prompts WHERE id=? AND scope=?{owner_filter} LIMIT 1",
                params,
            )
        return self._row_to_dict(row) if row else None

    async def get_any(
        self, prompt_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch a prompt from any scope — public first, then private."""
        for scope in ("public", "private"):
            result = await self.get(scope, prompt_id, owner_id=owner_id)
            if result:
                return result
        return None

    async def find_by_alias(
        self, alias: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Busca un prompt accesible por su alias exacto: primero uno propio
        del owner, si no un público. Evita traer todo el listado solo para
        resolver una mención "@alias" en el chat."""
        await self._ensure_migrated()
        alias = alias.strip().lower()
        async with open_db() as conn:
            row = None
            if owner_id:
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at FROM prompts "
                    "WHERE alias=? AND owner_id=? AND is_active=1 LIMIT 1",
                    (alias, owner_id),
                )
            if row is None:
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at FROM prompts "
                    "WHERE alias=? AND scope='public' AND is_active=1 LIMIT 1",
                    (alias,),
                )
        return self._row_to_dict(row) if row else None

    async def save(
        self, scope: str, payload: Dict[str, Any], owner_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if scope not in ("private", "public"):
            raise ValueError("scope must be private or public")
        if scope == "public" and not owner_id:
            raise ValueError("Los prompts públicos de sistema son de solo lectura")
        await self._ensure_migrated()

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        alias = str(payload.get("alias") or "").strip().lower()
        if not PROMPT_ALIAS_RE.match(alias):
            raise ValueError(
                "Alias inválido: usa minúsculas, números, guion o guion bajo, 3-30 caracteres"
            )
        prompt_id = payload.get("id") or generate_id()
        actual_owner = owner_id or "admin"
        now = _now()
        existing = await self.get_any(prompt_id, owner_id=actual_owner)
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
            raise ValueError("invalid prompt labels")
        data: Dict[str, Any] = {
            "id": prompt_id,
            "name": name,
            "resource_type": "prompt",
            "description": str(payload.get("description") or "").strip(),
            "icon": str(payload.get("icon") or "📝").strip(),
            "alias": alias,
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
            async with conn.transaction():
                dup = await conn.fetchone(
                    "SELECT 1 FROM prompts WHERE owner_id=? AND alias=? AND id != ?",
                    (actual_owner, alias, prompt_id),
                )
                if dup:
                    raise ValueError("Ya tienes un prompt con ese alias")
                await self._upsert(conn, prompt_id, actual_owner, scope, data)
        await self.sync_labels(prompt_id, actual_owner, data.get("labels") or [])
        return data

    async def delete(
        self,
        scope: str,
        prompt_id: str,
        owner_id: Optional[str] = None,
        allow_public: bool = False,
    ) -> bool:
        if scope == "public" and owner_id is None and not allow_public:
            raise ValueError("Los prompts públicos de sistema son de solo lectura")
        await self._ensure_migrated()

        async with open_db() as conn:
            if owner_id is not None:
                row = await conn.fetchone(
                    "SELECT id FROM prompts WHERE id=? AND scope=? AND owner_id=? LIMIT 1",
                    (prompt_id, scope, owner_id),
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM prompts WHERE id=? AND scope=? AND owner_id=?",
                    (prompt_id, scope, owner_id),
                )
            else:
                row = await conn.fetchone(
                    "SELECT id FROM prompts WHERE id=? AND scope=? LIMIT 1",
                    (prompt_id, scope),
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM prompts WHERE id=? AND scope=?", (prompt_id, scope)
                )
            await conn.commit()
        await self.clear_labels(prompt_id)
        return True

    async def unique_alias(self, owner_id: str, alias: str, exclude_id: str = "") -> str:
        """Devuelve ``alias`` si está libre para ``owner_id``, o un sufijo
        incremental (``alias-2``, ``alias-3``…) si ya existe. Usado por los
        flujos de clonado/enlace, donde el alias del prompt de origen puede
        colisionar con uno ya existente del destino — la copia nunca debe
        fallar por esto ni tocar la fila del propietario original."""
        await self._ensure_migrated()

        base = alias
        candidate = base
        n = 2
        async with open_db() as conn:
            while True:
                row = await conn.fetchone(
                    "SELECT 1 FROM prompts WHERE owner_id=? AND alias=? AND id != ?",
                    (owner_id, candidate, exclude_id),
                )
                if not row:
                    return candidate
                suffix = f"-{n}"
                trimmed = base[: 30 - len(suffix)].rstrip("-_") or base[:1]
                candidate = f"{trimmed}{suffix}"
                n += 1


# ─── MemoryStorage ────────────────────────────────────────────────────────────
# DB-backed. La propiedad interna se guarda con users.id.


def _safe_mem_id(filename: str) -> str:
    """Sanitiza el nombre de fichero de memoria para la DB."""
    name = re.sub(r"[^a-z0-9\-]", "-", filename.lower().removesuffix(".md")).strip("-")
    return name or "memory"


class MemoryStorage(LegacyMigrationStorage):
    """Async DB-backed memory storage (SQLite / PostgreSQL)."""
    def __init__(self, root_dir: Path) -> None:
        super().__init__()
        self._root_dir = Path(root_dir)  # solo para la migración única desde ficheros

    # ── one-time file→DB migration ───────────────────────────────────────────

    async def _migrate_legacy_data(self) -> None:

        async with open_db() as conn:
            try:
                count = await conn.fetchval("SELECT COUNT(*) FROM memory_files")
                if count:
                    return
            except Exception:
                return
            now = _now()
            for p in sorted(self._root_dir.glob("*.md")):
                try:
                    content = p.read_text(encoding="utf-8")
                    mem_id = p.stem
                    await conn.execute(
                        "INSERT OR IGNORE INTO memory_files (id, owner_id, content, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (mem_id, "admin", content, now),
                    )
                except Exception as exc:
                    flog.warning(f"[memory] Migración fallida {p}: {exc}")
            await conn.commit()

    # ── public API ───────────────────────────────────────────────────────────

    async def list(self, owner_id: str = "admin") -> List[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT id, content, updated_at FROM memory_files "
                "WHERE owner_id=? ORDER BY updated_at DESC",
                (owner_id,),
            )
        return [
            {
                "id": r["id"],
                "filename": f"{r['id']}.md",
                "size": len(r["content"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    async def get(self, filename: str, owner_id: str = "admin") -> Optional[str]:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)

        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT content FROM memory_files WHERE id=? AND owner_id=?",
                (mem_id, owner_id),
            )
        return row["content"] if row else None

    async def save(
        self, filename: str, content: str, owner_id: str = "admin"
    ) -> Dict[str, Any]:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)
        now = _now()

        async with open_db() as conn:
            if _db.IS_PG:
                await conn.execute(
                    "INSERT INTO memory_files (id, owner_id, content, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (id, owner_id) DO UPDATE SET content=EXCLUDED.content, updated_at=EXCLUDED.updated_at",
                    (mem_id, owner_id, content, now),
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO memory_files (id, owner_id, content, updated_at) VALUES (?, ?, ?, ?)",
                    (mem_id, owner_id, content, now),
                )
            await conn.commit()
        return {
            "id": mem_id,
            "filename": f"{mem_id}.md",
            "size": len(content),
            "updated_at": now,
        }

    async def delete(self, filename: str, owner_id: str = "admin") -> bool:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)

        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT id FROM memory_files WHERE id=? AND owner_id=?",
                (mem_id, owner_id),
            )
            if not row:
                return False
            await conn.execute(
                "DELETE FROM memory_files WHERE id=? AND owner_id=?", (mem_id, owner_id)
            )
            await conn.commit()
        return True
