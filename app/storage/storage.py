"""Storage: Agentes, Conexiones, Skills y Memoria."""
from __future__ import annotations

import json

from app.utils import flog
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.models.agent import Agent
from app.storage.crypto import decrypt, encrypt

import yaml


# ─── helpers ──────────────────────────────────────────────────────────────────

def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s or f"item-{uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── ConnectionStorage ────────────────────────────────────────────────────────

class ConnectionStorage:
    """DB-backed connection storage. Accepts the DB file path."""

    def __init__(self, db_path: Path) -> None:
        from app.storage.db import IS_PG, PH, close_db, open_db
        self._db_path = Path(db_path)
        self._IS_PG = IS_PG
        self._PH = PH
        self._open_db = open_db
        self._close_db = close_db
        self._migrate_json()

    def _conn(self):
        return self._open_db(self._db_path)

    def _migrate_json(self) -> None:
        """One-time import from connections.json if the table is empty."""
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM connections")
            if cur.fetchone()[0]:
                return
            from app.config.data import DATA_DIR
            old = DATA_DIR / "connections" / "connections.json"
            if not old.exists():
                return
            try:
                items = json.loads(old.read_text(encoding="utf-8"))
                for item in items:
                    self._upsert_with_conn(conn, item)
                old.rename(old.with_suffix(".migrated"))
            except Exception as exc:
                flog.warning(f"[storage] Migración de connections.json fallida: {exc}")
        finally:
            self._close_db(conn)

    def _upsert_with_conn(self, conn, payload: Dict[str, Any], owner_id: str = "admin") -> None:
        PH = self._PH
        conn_id = str(payload.get("id") or "").strip() or uuid4().hex[:12]
        payload["id"] = conn_id
        if self._IS_PG:
            conn.cursor().execute(
                f"INSERT INTO connections (id, owner_id, data, tokens_in, tokens_out, created_at, updated_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}) "
                f"ON CONFLICT (id) DO UPDATE SET owner_id=EXCLUDED.owner_id, data=EXCLUDED.data, "
                f"tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out, updated_at=EXCLUDED.updated_at",
                (
                    conn_id, owner_id,
                    json.dumps(payload, ensure_ascii=False),
                    int(payload.get("tokens_in") or 0),
                    int(payload.get("tokens_out") or 0),
                    str(payload.get("created_at") or _now()),
                    str(payload.get("updated_at") or _now()),
                ),
            )
        else:
            conn.execute(
                f"INSERT OR REPLACE INTO connections "
                f"(id, owner_id, data, tokens_in, tokens_out, created_at, updated_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
                (
                    conn_id, owner_id,
                    json.dumps(payload, ensure_ascii=False),
                    int(payload.get("tokens_in") or 0),
                    int(payload.get("tokens_out") or 0),
                    str(payload.get("created_at") or _now()),
                    str(payload.get("updated_at") or _now()),
                ),
            )
        conn.commit()

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        if d.get("api_key"):
            d["api_key"] = decrypt(d["api_key"])
        d["tokens_in"] = row["tokens_in"]
        d["tokens_out"] = row["tokens_out"]
        return d

    def list(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """owner_id=None → admin sees all. owner_id=str → own connections only."""
        PH = self._PH
        conn = self._conn()
        try:
            cur = conn.cursor()
            if owner_id is None:
                cur.execute("SELECT * FROM connections ORDER BY created_at ASC")
            else:
                cur.execute(
                    f"SELECT * FROM connections WHERE owner_id = {PH} ORDER BY created_at ASC",
                    (owner_id,),
                )
            rows = cur.fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            self._close_db(conn)

    def get(self, conn_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        PH = self._PH
        conn = self._conn()
        try:
            cur = conn.cursor()
            if owner_id is None:
                cur.execute(f"SELECT * FROM connections WHERE id = {PH}", (conn_id,))
            else:
                cur.execute(
                    f"SELECT * FROM connections WHERE id = {PH} AND owner_id = {PH}",
                    (conn_id, owner_id),
                )
            row = cur.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            self._close_db(conn)

    def save(self, payload: Dict[str, Any], owner_id: str = "admin") -> Dict[str, Any]:
        PH = self._PH
        conn_id = str(payload.get("id") or "").strip() or uuid4().hex[:12]
        payload["id"] = conn_id
        existing = self.get(conn_id, owner_id)
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
        conn = self._conn()
        try:
            if self._IS_PG:
                conn.cursor().execute(
                    f"INSERT INTO connections (id, owner_id, data, tokens_in, tokens_out, created_at, updated_at) "
                    f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}) "
                    f"ON CONFLICT (id) DO UPDATE SET owner_id=EXCLUDED.owner_id, data=EXCLUDED.data, "
                    f"tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out, updated_at=EXCLUDED.updated_at",
                    (
                        conn_id, owner_id,
                        json.dumps(stored, ensure_ascii=False),
                        int(stored.get("tokens_in") or 0),
                        int(stored.get("tokens_out") or 0),
                        stored["created_at"],
                        stored["updated_at"],
                    ),
                )
            else:
                conn.execute(
                    f"INSERT OR REPLACE INTO connections "
                    f"(id, owner_id, data, tokens_in, tokens_out, created_at, updated_at) "
                    f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
                    (
                        conn_id, owner_id,
                        json.dumps(stored, ensure_ascii=False),
                        int(stored.get("tokens_in") or 0),
                        int(stored.get("tokens_out") or 0),
                        stored["created_at"],
                        stored["updated_at"],
                    ),
                )
            conn.commit()
        finally:
            self._close_db(conn)
        return payload

    def add_tokens(self, conn_id: str, input_tokens: int, output_tokens: int) -> None:
        PH = self._PH
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE connections "
                f"SET tokens_in = tokens_in + {PH}, tokens_out = tokens_out + {PH} "
                f"WHERE id = {PH}",
                (input_tokens, output_tokens, conn_id),
            )
            conn.commit()
            cur.execute(
                f"SELECT data, tokens_in, tokens_out FROM connections WHERE id = {PH}",
                (conn_id,),
            )
            row = cur.fetchone()
            if row:
                d: Dict[str, Any] = json.loads(row["data"])
                d["tokens_in"] = row["tokens_in"]
                d["tokens_out"] = row["tokens_out"]
                cur.execute(
                    f"UPDATE connections SET data = {PH} WHERE id = {PH}",
                    (json.dumps(d, ensure_ascii=False), conn_id),
                )
                conn.commit()
        finally:
            self._close_db(conn)

    def delete(self, conn_id: str, owner_id: Optional[str] = None) -> bool:
        PH = self._PH
        conn = self._conn()
        try:
            cur = conn.cursor()
            if owner_id is None:
                cur.execute(f"DELETE FROM connections WHERE id = {PH}", (conn_id,))
            else:
                cur.execute(
                    f"DELETE FROM connections WHERE id = {PH} AND owner_id = {PH}",
                    (conn_id, owner_id),
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            self._close_db(conn)


# ─── AgentStorage ─────────────────────────────────────────────────────────────
# Agentes públicos:  data/agents/public/<slug>/config.json  (solo lectura en UI)
# Agentes privados: data/agents/private/<slug>/config.json (CRUD completo)

class AgentStorage:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        (self.root_dir / "public").mkdir(parents=True, exist_ok=True)
        priv = self.root_dir / "private"
        priv.mkdir(parents=True, exist_ok=True)
        # migrar agentes planos (estructura antigua) a private/
        for p in list(self.root_dir.glob("*/config.json")):
            agent_dir = p.parent
            if agent_dir.name not in ("public", "private"):
                dest = priv / agent_dir.name
                if not dest.exists():
                    agent_dir.rename(dest)

    def _dir(self, scope: str, agent_id: str) -> Path:
        safe = re.sub(r"[^a-z0-9_\-]", "-", agent_id.lower()).strip("-")
        return self.root_dir / scope / safe

    def _path(self, scope: str, agent_id: str) -> Path:
        return self._dir(scope, agent_id) / "config.json"

    def list(self, scope: str = "all") -> List[Dict[str, Any]]:
        items = []
        scopes = []
        if scope in ("all", "public"):
            scopes.append("public")
        if scope in ("all", "private"):
            scopes.append("private")
        for s in scopes:
            for p in sorted((self.root_dir / s).glob("*/config.json")):
                try:
                    a = json.loads(p.read_text(encoding="utf-8"))
                    a["scope"] = s
                    items.append(self._summary(a))
                except Exception as exc:
                    flog.warning(f"[storage] Agente corrupto en {p}: {exc}")
                    continue
        return items

    def get(self, agent_id: str, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
        scopes_to_try = [scope] if scope else ["public", "private"]
        for s in scopes_to_try:
            p = self._path(s, agent_id)
            if p.exists():
                a = json.loads(p.read_text(encoding="utf-8"))
                a["scope"] = s
                return a
        return None

    def save(self, payload: Dict[str, Any], scope: str = "private", owner_id: Optional[str] = None) -> Dict[str, Any]:
        if scope == "public":
            raise ValueError("Los agentes públicos son de solo lectura")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        folder_id = payload.get("folder_id") or None
        agent_id = _slug(name)
        now = _now()

        # When a folder is specified and the base slug already exists in a different folder,
        # generate a unique ID so both agents can coexist.
        if folder_id:
            existing_path = self._path(scope, agent_id)
            if existing_path.exists():
                try:
                    existing_data = json.loads(existing_path.read_text(encoding="utf-8"))
                    if existing_data.get("folder_id") != folder_id:
                        agent_id = f"{agent_id}-{uuid4().hex[:6]}"
                except Exception:
                    pass

        agent = Agent.from_dict({**payload, "id": agent_id, "scope": scope, "folder_id": folder_id})
        d = self._dir(scope, agent_id)
        d.mkdir(exist_ok=True)
        p = d / "config.json"
        if p.exists():
            existing = json.loads(p.read_text(encoding="utf-8"))
            agent.created_at = existing.get("created_at", now)
            agent.owner_id = existing.get("owner_id") or owner_id
        else:
            agent.created_at = now
            agent.owner_id = owner_id
        agent.updated_at = now
        p.write_text(json.dumps(agent.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return agent.to_dict()

    def move_folder(self, agent_id: str, folder_id: Optional[str]) -> bool:
        p = self._path("private", agent_id)
        if not p.exists():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        if folder_id:
            data["folder_id"] = folder_id
        else:
            data.pop("folder_id", None)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True

    def add_tokens(self, agent_id: str, tokens_in: int, tokens_out: int) -> None:
        p = self._path("private", agent_id)
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        data["tokens_in"] = int(data.get("tokens_in") or 0) + tokens_in
        data["tokens_out"] = int(data.get("tokens_out") or 0) + tokens_out
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_model(self, agent_id: str, scope: Optional[str] = None) -> Optional[Agent]:
        """Typed accessor — returns the correct Agent subclass."""
        data = self.get(agent_id, scope)
        return Agent.from_dict(data) if data else None

    def delete(self, agent_id: str, scope: Optional[str] = None) -> bool:
        scopes_to_try = [scope] if scope else ["private"]
        for s in scopes_to_try:
            if s == "public":
                raise ValueError("Los agentes públicos son de solo lectura")
            d = self._dir(s, agent_id)
            if d.exists():
                shutil.rmtree(d)
                return True
        return False

    def _summary(self, a: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": a["id"],
            "name": a.get("name", a["id"]),
            "agent_type": a.get("agent_type", "generic"),
            "description": a.get("description", ""),
            "icon": a.get("icon", ""),
            "tags": a.get("tags", []),
            "language": a.get("language", ""),
            "connection_id": a.get("connection_id"),
            "model": a.get("model", ""),
            "temperature": a.get("temperature", 0.7),
            "max_tokens": a.get("max_tokens"),
            "timeout": a.get("timeout", 120),
            "skills": a.get("skills", []),
            "use_memory": a.get("use_memory", False),
            "memory_file": a.get("memory_file"),
            "folder_id": a.get("folder_id"),
            "scope": a.get("scope", "private"),
            "created_at": a.get("created_at"),
            "updated_at": a.get("updated_at"),
            "owner_id": a.get("owner_id"),
        }


# ─── SkillStorage ─────────────────────────────────────────────────────────────
# Skills públicas:  data/skills/public/<slug>/SKILL.md  (solo lectura en la UI)
# Skills privadas: data/skills/private/<slug>/SKILL.md (CRUD completo)

class SkillStorage:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        (self.root_dir / "public").mkdir(parents=True, exist_ok=True)
        (self.root_dir / "private").mkdir(parents=True, exist_ok=True)

    def _read(self, path: Path) -> Dict[str, Any]:
        raw = path.read_text(encoding="utf-8")
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip() if len(parts) > 2 else ""
        else:
            meta = {}
            body = raw.strip()
        meta["content"] = body
        meta.setdefault("id", path.parent.name)
        meta.setdefault("name", path.parent.name)
        return meta

    def _write(self, path: Path, payload: Dict[str, Any]) -> None:
        content = payload.pop("content", "")
        front = yaml.dump(payload, allow_unicode=True, default_flow_style=False).strip()
        path.write_text(f"---\n{front}\n---\n\n{content}\n", encoding="utf-8")

    def list(self, scope: str = "all") -> List[Dict[str, Any]]:
        """scope: 'all' | 'public' | 'private'"""
        items: List[Dict[str, Any]] = []
        scopes = []
        if scope in ("all", "public"):
            scopes.append("public")
        if scope in ("all", "private"):
            scopes.append("private")
        for s in scopes:
            for p in sorted((self.root_dir / s).glob("*/SKILL.md")):
                try:
                    skill = self._read(p)
                    skill["scope"] = s
                    skill.setdefault("folder_id", None)
                    items.append({k: v for k, v in skill.items() if k != "content"})
                except Exception:
                    continue
        return items

    def _safe_path(self, *parts: str) -> "Path":
        p = (self.root_dir / Path(*parts)).resolve()
        if not str(p).startswith(str(self.root_dir.resolve())):
            raise ValueError("Path fuera del directorio de skills")
        return p

    def get(self, scope: str, skill_id: str) -> Optional[Dict[str, Any]]:
        # 1) path exacto con el id tal cual
        p = self._safe_path(scope, skill_id, "SKILL.md")
        if not p.exists():
            # 2) path con el id slugificado
            p = self._safe_path(scope, _slug(skill_id), "SKILL.md")
        if not p.exists():
            # 3) escanear todos los SKILL.md buscando id coincidente en frontmatter
            scope_dir = self.root_dir / scope
            for candidate in scope_dir.glob("*/SKILL.md"):
                try:
                    meta = self._read(candidate)
                    if str(meta.get("id") or "").upper() == skill_id.upper():
                        skill = meta
                        skill["scope"] = scope
                        return skill
                except Exception:
                    continue
            return None
        skill = self._read(p)
        skill["scope"] = scope
        return skill

    def get_any(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a skill from any scope. Used for team-shared access."""
        for scope in ("public", "private"):
            result = self.get(scope, skill_id)
            if result:
                return result
        return None

    def save(self, scope: str, payload: Dict[str, Any], owner_id: Optional[str] = None) -> Dict[str, Any]:
        if scope == "public":
            raise ValueError("Las skills públicas son de solo lectura")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        skill_id = _slug(name)
        d = self.root_dir / scope / skill_id
        d.mkdir(exist_ok=True)
        folder_id = payload.get("folder_id") or None
        data: Dict[str, Any] = {
            "id": skill_id,
            "name": name,
            "description": str(payload.get("description") or "").strip(),
            "icon": str(payload.get("icon") or "🔧").strip(),
            "category": str(payload.get("category") or "").strip() or None,
            "content": str(payload.get("content") or "").strip(),
        }
        if folder_id:
            data["folder_id"] = folder_id
        if owner_id:
            data["owner_id"] = owner_id
        self._write(d / "SKILL.md", data)
        result = self._read(d / "SKILL.md")
        result["scope"] = scope
        result.setdefault("folder_id", None)
        return result

    def move_folder(self, skill_id: str, folder_id: Optional[str]) -> bool:
        p = self._safe_path("private", skill_id, "SKILL.md")
        if not p.exists():
            p = self._safe_path("private", _slug(skill_id), "SKILL.md")
        if not p.exists():
            return False
        data = self._read(p)
        if folder_id:
            data["folder_id"] = folder_id
        else:
            data.pop("folder_id", None)
        self._write(p, data)
        return True

    def delete(self, scope: str, skill_id: str) -> bool:
        if scope == "public":
            raise ValueError("Las skills públicas son de solo lectura")
        d = self._safe_path(scope, skill_id)
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True


# ─── MemoryStorage ────────────────────────────────────────────────────────────
# Archivos de memoria: data/memory/<slug>.md  (texto plano Markdown)

class MemoryStorage:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.root_dir / "_folder_meta.json"

    def _safe_name(self, filename: str) -> str:
        """Sanitiza el nombre de fichero para prevenir path traversal."""
        name = re.sub(r"[^a-z0-9\-]", "-", filename.lower().removesuffix(".md")).strip("-")
        name = name or "memory"
        return f"{name}.md"

    def _load_meta(self) -> Dict[str, Any]:
        if self._meta_path.exists():
            try:
                import json
                return json.loads(self._meta_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_meta(self, meta: Dict[str, Any]) -> None:
        import json
        self._meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def list(self) -> List[Dict[str, Any]]:
        meta = self._load_meta()
        items = []
        for p in sorted(self.root_dir.glob("*.md")):
            stat = p.stat()
            items.append({
                "id": p.stem,
                "filename": p.name,
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "folder_id": meta.get(p.name),
            })
        return items

    def get(self, filename: str) -> Optional[str]:
        p = self.root_dir / self._safe_name(filename)
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")

    def save(self, filename: str, content: str) -> Dict[str, Any]:
        filename = self._safe_name(filename)
        p = self.root_dir / filename
        p.write_text(content, encoding="utf-8")
        stat = p.stat()
        meta = self._load_meta()
        return {
            "id": p.stem,
            "filename": p.name,
            "size": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "folder_id": meta.get(p.name),
        }

    def move_folder(self, filename: str, folder_id: Optional[str]) -> bool:
        safe = self._safe_name(filename)
        p = self.root_dir / safe
        if not p.exists():
            return False
        meta = self._load_meta()
        if folder_id:
            meta[safe] = folder_id
        else:
            meta.pop(safe, None)
        self._save_meta(meta)
        return True

    def delete(self, filename: str) -> bool:
        safe = self._safe_name(filename)
        p = self.root_dir / safe
        if not p.exists():
            return False
        p.unlink()
        meta = self._load_meta()
        if safe in meta:
            meta.pop(safe)
            self._save_meta(meta)
        return True
