"""Storage: Agentes, Conexiones, Skills y Memoria."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict
from uuid import uuid4

from app.models.agent import Agent
from app.storage.crypto import decrypt, encrypt
from app.utils import flog
from app.utils import now_iso as _now

import yaml


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
    folder_id: Optional[str]
    scope: str
    created_at: Optional[str]
    updated_at: Optional[str]
    owner_id: Optional[str]


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s or f"item-{uuid4().hex[:8]}"


# ─── ConnectionStorage ────────────────────────────────────────────────────────


class ConnectionStorage:
    """DB-backed async connection storage."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)  # informational only
        self._migrated = False
        self._migrate_lock: "asyncio.Lock | None" = (
            None  # created lazily (needs running loop)
        )

    async def _migrate_json(self) -> None:
        """One-time import from connections.json if table is empty."""
        from app.storage.db import open_db
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

    async def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        if self._migrate_lock is None:
            self._migrate_lock = asyncio.Lock()
        async with self._migrate_lock:
            if not self._migrated:
                self._migrated = True
                await self._migrate_json()

    async def _upsert(
        self, conn: Any, payload: Dict[str, Any], owner_id: str = "admin"
    ) -> None:
        """Insert or replace a connection row (uses AsyncConn, ? placeholders)."""
        from app.storage.db import IS_PG

        conn_id = str(payload.get("id") or "").strip() or uuid4().hex[:12]
        payload["id"] = conn_id
        if IS_PG:
            await conn.execute(
                "INSERT INTO connections (id, owner_id, data, tokens_in, tokens_out, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET owner_id=EXCLUDED.owner_id, data=EXCLUDED.data, "
                "tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out, updated_at=EXCLUDED.updated_at",
                (
                    conn_id,
                    owner_id,
                    json.dumps(payload, ensure_ascii=False),
                    int(payload.get("tokens_in") or 0),
                    int(payload.get("tokens_out") or 0),
                    str(payload.get("created_at") or _now()),
                    str(payload.get("updated_at") or _now()),
                ),
            )
        else:
            await conn.execute(
                "INSERT OR REPLACE INTO connections "
                "(id, owner_id, data, tokens_in, tokens_out, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    conn_id,
                    owner_id,
                    json.dumps(payload, ensure_ascii=False),
                    int(payload.get("tokens_in") or 0),
                    int(payload.get("tokens_out") or 0),
                    str(payload.get("created_at") or _now()),
                    str(payload.get("updated_at") or _now()),
                ),
            )

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        if d.get("api_key"):
            d["api_key"] = decrypt(d["api_key"])
        d["tokens_in"] = row["tokens_in"]
        d["tokens_out"] = row["tokens_out"]
        return d

    async def list(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """owner_id=None → admin sees all. owner_id=str → own connections only."""
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
            if owner_id is None:
                rows = await conn.fetchall(
                    "SELECT id, owner_id, data, tokens_in, tokens_out FROM connections ORDER BY created_at ASC"
                )
            else:
                rows = await conn.fetchall(
                    "SELECT id, owner_id, data, tokens_in, tokens_out FROM connections "
                    "WHERE owner_id = ? ORDER BY created_at ASC",
                    (owner_id,),
                )
        return [self._row_to_dict(r) for r in rows]

    async def get(
        self, conn_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        from app.storage.db import open_db

        async with open_db() as conn:
            if owner_id is None:
                row = await conn.fetchone(
                    "SELECT id, owner_id, data, tokens_in, tokens_out FROM connections WHERE id = ?",
                    (conn_id,),
                )
            else:
                row = await conn.fetchone(
                    "SELECT id, owner_id, data, tokens_in, tokens_out FROM connections "
                    "WHERE id = ? AND owner_id = ?",
                    (conn_id, owner_id),
                )
        return self._row_to_dict(row) if row else None

    async def save(
        self, payload: Dict[str, Any], owner_id: str = "admin"
    ) -> Dict[str, Any]:
        from app.storage.db import open_db

        conn_id = str(payload.get("id") or "").strip() or uuid4().hex[:12]
        payload["id"] = conn_id
        existing = await self.get(conn_id, owner_id)
        if existing:
            payload["created_at"] = existing.get("created_at", _now())
            if not payload.get("api_key") and existing.get("api_key"):
                payload["api_key"] = existing["api_key"]
        else:
            payload.setdefault("created_at", _now())
        payload["updated_at"] = _now()
        stored = dict(payload)
        if stored.get("api_key"):
            stored["api_key"] = encrypt(stored["api_key"])
        async with open_db() as conn:
            await self._upsert(conn, stored, owner_id)
            await conn.commit()
        return payload

    async def delete(self, conn_id: str, owner_id: Optional[str] = None) -> bool:
        from app.storage.db import open_db

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
        return True

    async def add_tokens(
        self, conn_id: str, input_tokens: int, output_tokens: int
    ) -> None:
        from app.storage.db import IS_PG, open_db

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
                        if IS_PG:
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


class AgentStorage:
    """Async DB-backed agent storage (SQLite / PostgreSQL)."""

    def __init__(self, root_dir: Path) -> None:
        self._root_dir = Path(root_dir)  # solo para la migración única desde ficheros
        self._migrated = False
        self._migrate_lock: "asyncio.Lock | None" = None

    # ── one-time file→DB migration ───────────────────────────────────────────

    async def _migrate_files(self) -> None:
        from app.storage.db import open_db

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

    async def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        if self._migrate_lock is None:
            self._migrate_lock = asyncio.Lock()
        async with self._migrate_lock:
            if not self._migrated:
                self._migrated = True
                await self._migrate_files()

    # ── internal helpers ─────────────────────────────────────────────────────

    async def _upsert(
        self, conn: Any, agent_id: str, owner_id: str, scope: str, data: Dict[str, Any]
    ) -> None:
        from app.storage.db import IS_PG

        data_json = json.dumps(data, ensure_ascii=False)
        tokens_in = int(data.get("tokens_in") or 0)
        tokens_out = int(data.get("tokens_out") or 0)
        folder_id = data.get("folder_id")
        now = _now()
        created_at = str(data.get("created_at") or now)
        updated_at = str(data.get("updated_at") or now)
        if IS_PG:
            await conn.execute(
                "INSERT INTO agents (id, owner_id, scope, data, tokens_in, tokens_out, folder_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET scope=EXCLUDED.scope, data=EXCLUDED.data, "
                "tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out, "
                "folder_id=EXCLUDED.folder_id, updated_at=EXCLUDED.updated_at",
                (
                    agent_id,
                    owner_id,
                    scope,
                    data_json,
                    tokens_in,
                    tokens_out,
                    folder_id,
                    created_at,
                    updated_at,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR REPLACE INTO agents "
                "(id, owner_id, scope, data, tokens_in, tokens_out, folder_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    owner_id,
                    scope,
                    data_json,
                    tokens_in,
                    tokens_out,
                    folder_id,
                    created_at,
                    updated_at,
                ),
            )

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        d["tokens_in"] = row["tokens_in"]
        d["tokens_out"] = row["tokens_out"]
        d["folder_id"] = row["folder_id"]
        d["scope"] = row["scope"]
        owner = row["owner_id"]
        d["owner_id"] = None if owner == _PUBLIC_OWNER else owner
        return d

    # ── public API ───────────────────────────────────────────────────────────

    async def list(
        self, scope: str = "all", owner_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
            if scope == "public":
                rows = await conn.fetchall(
                    "SELECT id, owner_id, scope, data, tokens_in, tokens_out, folder_id "
                    "FROM agents WHERE scope='public' ORDER BY created_at ASC"
                )
            elif scope == "private":
                if owner_id:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, scope, data, tokens_in, tokens_out, folder_id "
                        "FROM agents WHERE scope='private' AND owner_id=? ORDER BY created_at ASC",
                        (owner_id,),
                    )
                else:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, scope, data, tokens_in, tokens_out, folder_id "
                        "FROM agents WHERE scope='private' ORDER BY created_at ASC"
                    )
            else:  # all
                rows = await conn.fetchall(
                    "SELECT id, owner_id, scope, data, tokens_in, tokens_out, folder_id "
                    "FROM agents ORDER BY created_at ASC"
                )
        return [self._row_to_dict(r) for r in rows]

    async def get(
        self, agent_id: str, scope: Optional[str] = None, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
            if scope == "public":
                row = await conn.fetchone(
                    "SELECT id, owner_id, scope, data, tokens_in, tokens_out, folder_id "
                    "FROM agents WHERE id=? AND scope='public' LIMIT 1",
                    (agent_id,),
                )
            elif scope == "private":
                row = await conn.fetchone(
                    "SELECT id, owner_id, scope, data, tokens_in, tokens_out, folder_id "
                    "FROM agents WHERE id=? AND scope='private' LIMIT 1",
                    (agent_id,),
                )
            else:
                # prefer private, fall back to public
                row = await conn.fetchone(
                    "SELECT id, owner_id, scope, data, tokens_in, tokens_out, folder_id "
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
        if scope == "public":
            raise ValueError("Los agentes públicos son de solo lectura")
        await self._ensure_migrated()
        from app.storage.db import open_db

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        agent_id = payload.get("id") or _slug(name)
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
            }
        )
        data = agent.to_dict()
        async with open_db() as conn:
            await self._upsert(conn, agent_id, actual_owner, scope, data)
            await conn.commit()
        return data

    async def move_folder(
        self, agent_id: str, folder_id: Optional[str], owner_id: Optional[str] = None
    ) -> bool:
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT data FROM agents WHERE id=? AND scope='private' LIMIT 1",
                (agent_id,),
            )
            if not row:
                return False
            data = json.loads(row["data"])
            if folder_id:
                data["folder_id"] = folder_id
            else:
                data.pop("folder_id", None)
            await conn.execute(
                "UPDATE agents SET folder_id=?, data=?, updated_at=? WHERE id=? AND scope='private'",
                (folder_id, json.dumps(data, ensure_ascii=False), _now(), agent_id),
            )
            await conn.commit()
        return True

    async def add_tokens(
        self,
        agent_id: str,
        tokens_in: int,
        tokens_out: int,
        owner_id: Optional[str] = None,
    ) -> None:
        from app.storage.db import open_db

        async with open_db() as conn:
            await conn.execute(
                "UPDATE agents SET tokens_in=tokens_in+?, tokens_out=tokens_out+? "
                "WHERE id=? AND scope='private'",
                (tokens_in, tokens_out, agent_id),
            )
            await conn.commit()

    async def delete(
        self, agent_id: str, scope: Optional[str] = None, owner_id: Optional[str] = None
    ) -> bool:
        await self._ensure_migrated()
        if scope == "public":
            raise ValueError("Los agentes públicos son de solo lectura")
        from app.storage.db import open_db

        async with open_db() as conn:
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
            "folder_id": a.get("folder_id"),
            "scope": scope,
            "created_at": a.get("created_at"),
            "updated_at": a.get("updated_at"),
            "owner_id": a.get("owner_id"),
        }


# ─── SkillStorage ─────────────────────────────────────────────────────────────
# DB-backed. owner_id='__public__' para skills de sistema/públicas.


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


class SkillStorage:
    """Async DB-backed skill storage (SQLite / PostgreSQL)."""

    def __init__(self, root_dir: Path) -> None:
        self._root_dir = Path(root_dir)  # solo para la migración única desde ficheros
        self._migrated = False
        self._migrate_lock: "asyncio.Lock | None" = None

    # ── one-time file→DB migration ───────────────────────────────────────────

    async def _migrate_files(self) -> None:
        from app.storage.db import open_db

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

    async def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        if self._migrate_lock is None:
            self._migrate_lock = asyncio.Lock()
        async with self._migrate_lock:
            if not self._migrated:
                self._migrated = True
                await self._migrate_files()

    # ── internal helpers ─────────────────────────────────────────────────────

    async def _upsert(
        self, conn: Any, skill_id: str, owner_id: str, scope: str, data: Dict[str, Any]
    ) -> None:
        from app.storage.db import IS_PG

        content = str(data.get("content") or "")
        folder_id = data.get("folder_id")
        now = _now()
        created_at = str(data.get("created_at") or now)
        updated_at = str(data.get("updated_at") or now)
        meta = {k: v for k, v in data.items() if k not in ("content",)}
        meta_json = json.dumps(meta, ensure_ascii=False)
        if IS_PG:
            await conn.execute(
                "INSERT INTO skills (id, owner_id, scope, data, content, folder_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET scope=EXCLUDED.scope, data=EXCLUDED.data, "
                "content=EXCLUDED.content, folder_id=EXCLUDED.folder_id, updated_at=EXCLUDED.updated_at",
                (
                    skill_id,
                    owner_id,
                    scope,
                    meta_json,
                    content,
                    folder_id,
                    created_at,
                    updated_at,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR REPLACE INTO skills "
                "(id, owner_id, scope, data, content, folder_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    skill_id,
                    owner_id,
                    scope,
                    meta_json,
                    content,
                    folder_id,
                    created_at,
                    updated_at,
                ),
            )

    def _row_to_dict(self, row: Any, include_content: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        if include_content:
            d["content"] = row["content"]
        d["scope"] = row["scope"]
        d["folder_id"] = row["folder_id"]
        owner = row["owner_id"]
        d["owner_id"] = None if owner == _PUBLIC_OWNER else owner
        return d

    # ── public API ───────────────────────────────────────────────────────────

    async def list(
        self, scope: str = "all", owner_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
            if scope == "public":
                rows = await conn.fetchall(
                    "SELECT id, owner_id, scope, data, content, folder_id "
                    "FROM skills WHERE scope='public' ORDER BY created_at ASC"
                )
            elif scope == "private":
                if owner_id:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, scope, data, content, folder_id "
                        "FROM skills WHERE scope='private' AND owner_id=? ORDER BY created_at ASC",
                        (owner_id,),
                    )
                else:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, scope, data, content, folder_id "
                        "FROM skills WHERE scope='private' ORDER BY created_at ASC"
                    )
            else:  # all
                rows = await conn.fetchall(
                    "SELECT id, owner_id, scope, data, content, folder_id "
                    "FROM skills ORDER BY created_at ASC"
                )
        return [self._row_to_dict(r, include_content=False) for r in rows]

    async def get(
        self, scope: str, skill_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT id, owner_id, scope, data, content, folder_id "
                "FROM skills WHERE id=? AND scope=? LIMIT 1",
                (skill_id, scope),
            )
            if not row:
                # try slug variant
                row = await conn.fetchone(
                    "SELECT id, owner_id, scope, data, content, folder_id "
                    "FROM skills WHERE id=? AND scope=? LIMIT 1",
                    (_slug(skill_id), scope),
                )
        return self._row_to_dict(row) if row else None

    async def get_any(
        self, skill_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch a skill from any scope — public first, then private."""
        for scope in ("public", "private"):
            result = await self.get(scope, skill_id)
            if result:
                return result
        return None

    async def save(
        self, scope: str, payload: Dict[str, Any], owner_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if scope == "public":
            raise ValueError("Las skills públicas son de solo lectura")
        await self._ensure_migrated()
        from app.storage.db import open_db

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        skill_id = payload.get("id") or _slug(name)
        actual_owner = owner_id or "admin"
        now = _now()
        existing = await self.get(scope, skill_id)
        data: Dict[str, Any] = {
            "id": skill_id,
            "name": name,
            "description": str(payload.get("description") or "").strip(),
            "icon": str(payload.get("icon") or "🔧").strip(),
            "category": str(payload.get("category") or "").strip() or None,
            "content": str(payload.get("content") or "").strip(),
            "tags": [
                str(t).strip().lower()
                for t in (payload.get("tags") or [])
                if str(t).strip()
            ],
            "labels": [
                str(lbl) for lbl in (payload.get("labels") or ["private"]) if lbl
            ],
            "scope": scope,
            "owner_id": actual_owner,
            "folder_id": payload.get("folder_id") or None,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        async with open_db() as conn:
            await self._upsert(conn, skill_id, actual_owner, scope, data)
            await conn.commit()
        data.setdefault("folder_id", None)
        return data

    async def move_folder(
        self, skill_id: str, folder_id: Optional[str], owner_id: Optional[str] = None
    ) -> bool:
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT data FROM skills WHERE id=? AND scope='private' LIMIT 1",
                (skill_id,),
            )
            if not row:
                row = await conn.fetchone(
                    "SELECT data FROM skills WHERE id=? AND scope='private' LIMIT 1",
                    (_slug(skill_id),),
                )
            if not row:
                return False
            meta = json.loads(row["data"])
            if folder_id:
                meta["folder_id"] = folder_id
            else:
                meta.pop("folder_id", None)
            await conn.execute(
                "UPDATE skills SET folder_id=?, data=?, updated_at=? WHERE id=? AND scope='private'",
                (folder_id, json.dumps(meta, ensure_ascii=False), _now(), skill_id),
            )
            await conn.commit()
        return True

    async def delete(
        self, scope: str, skill_id: str, owner_id: Optional[str] = None
    ) -> bool:
        if scope == "public":
            raise ValueError("Las skills públicas son de solo lectura")
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
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
        return True


# ─── MemoryStorage ────────────────────────────────────────────────────────────
# DB-backed. Memoria por usuario: owner_id=username.


def _safe_mem_id(filename: str) -> str:
    """Sanitiza el nombre de fichero de memoria para la DB."""
    name = re.sub(r"[^a-z0-9\-]", "-", filename.lower().removesuffix(".md")).strip("-")
    return name or "memory"


class MemoryStorage:
    """Async DB-backed memory storage (SQLite / PostgreSQL)."""

    def __init__(self, root_dir: Path) -> None:
        self._root_dir = Path(root_dir)  # solo para la migración única desde ficheros
        self._migrated = False
        self._migrate_lock: "asyncio.Lock | None" = None

    # ── one-time file→DB migration ───────────────────────────────────────────

    async def _migrate_files(self) -> None:
        from app.storage.db import open_db

        async with open_db() as conn:
            try:
                count = await conn.fetchval("SELECT COUNT(*) FROM memory_files")
                if count:
                    return
            except Exception:
                return
            meta_path = self._root_dir / "_folder_meta.json"
            folder_meta: Dict[str, Any] = {}
            if meta_path.exists():
                try:
                    folder_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            now = _now()
            for p in sorted(self._root_dir.glob("*.md")):
                try:
                    content = p.read_text(encoding="utf-8")
                    mem_id = p.stem
                    folder_id = folder_meta.get(p.name)
                    await conn.execute(
                        "INSERT OR IGNORE INTO memory_files (id, owner_id, content, folder_id, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (mem_id, "admin", content, folder_id, now),
                    )
                except Exception as exc:
                    flog.warning(f"[memory] Migración fallida {p}: {exc}")
            await conn.commit()

    async def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        if self._migrate_lock is None:
            self._migrate_lock = asyncio.Lock()
        async with self._migrate_lock:
            if not self._migrated:
                self._migrated = True
                await self._migrate_files()

    # ── public API ───────────────────────────────────────────────────────────

    async def list(self, owner_id: str = "admin") -> List[Dict[str, Any]]:
        await self._ensure_migrated()
        from app.storage.db import open_db

        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT id, content, folder_id, updated_at FROM memory_files "
                "WHERE owner_id=? ORDER BY updated_at DESC",
                (owner_id,),
            )
        return [
            {
                "id": r["id"],
                "filename": f"{r['id']}.md",
                "size": len(r["content"]),
                "updated_at": r["updated_at"],
                "folder_id": r["folder_id"],
            }
            for r in rows
        ]

    async def get(self, filename: str, owner_id: str = "admin") -> Optional[str]:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)
        from app.storage.db import open_db

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
        from app.storage.db import IS_PG, open_db

        async with open_db() as conn:
            if IS_PG:
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
            "folder_id": None,
        }

    async def move_folder(
        self, filename: str, folder_id: Optional[str], owner_id: str = "admin"
    ) -> bool:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)
        from app.storage.db import open_db

        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT id FROM memory_files WHERE id=? AND owner_id=?",
                (mem_id, owner_id),
            )
            if not row:
                return False
            await conn.execute(
                "UPDATE memory_files SET folder_id=?, updated_at=? WHERE id=? AND owner_id=?",
                (folder_id, _now(), mem_id, owner_id),
            )
            await conn.commit()
        return True

    async def delete(self, filename: str, owner_id: str = "admin") -> bool:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)
        from app.storage.db import open_db

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
