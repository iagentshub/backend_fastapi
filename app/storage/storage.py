"""Storage: Agentes, Conexiones, Skills y Memoria."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.models.agent import Agent

import yaml


# ─── helpers ──────────────────────────────────────────────────────────────────

def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s or f"item-{uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── ConnectionStorage ────────────────────────────────────────────────────────

class ConnectionStorage:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("[]", encoding="utf-8")

    def _load(self) -> List[Dict[str, Any]]:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _save(self, items: List[Dict[str, Any]]) -> None:
        self._path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    def list(self) -> List[Dict[str, Any]]:
        return self._load()

    def get(self, conn_id: str) -> Optional[Dict[str, Any]]:
        return next((c for c in self._load() if c.get("id") == conn_id), None)

    def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        items = self._load()
        conn_id = str(payload.get("id") or "").strip() or uuid4().hex[:12]
        payload["id"] = conn_id
        payload.setdefault("created_at", _now())
        payload["updated_at"] = _now()
        idx = next((i for i, c in enumerate(items) if c.get("id") == conn_id), None)
        if idx is not None:
            existing = items[idx]
            payload["created_at"] = existing.get("created_at", payload["created_at"])
            if not payload.get("api_key") and existing.get("api_key"):
                payload["api_key"] = existing["api_key"]
            items[idx] = payload
        else:
            items.append(payload)
        self._save(items)
        return payload

    def add_tokens(self, conn_id: str, input_tokens: int, output_tokens: int) -> None:
        items = self._load()
        idx = next((i for i, c in enumerate(items) if c.get("id") == conn_id), None)
        if idx is None:
            return
        items[idx].setdefault("tokens_in", 0)
        items[idx].setdefault("tokens_out", 0)
        items[idx]["tokens_in"] += input_tokens
        items[idx]["tokens_out"] += output_tokens
        self._save(items)

    def delete(self, conn_id: str) -> bool:
        items = self._load()
        new = [c for c in items if c.get("id") != conn_id]
        if len(new) == len(items):
            return False
        self._save(new)
        return True


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
                except Exception:
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

    def save(self, payload: Dict[str, Any], scope: str = "private") -> Dict[str, Any]:
        if scope == "public":
            raise ValueError("Los agentes públicos son de solo lectura")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        agent_id = _slug(name)
        now = _now()
        agent = Agent.from_dict({**payload, "id": agent_id, "scope": scope})
        d = self._dir(scope, agent_id)
        d.mkdir(exist_ok=True)
        p = d / "config.json"
        if p.exists():
            existing = json.loads(p.read_text(encoding="utf-8"))
            agent.created_at = existing.get("created_at", now)
        else:
            agent.created_at = now
        agent.updated_at = now
        p.write_text(json.dumps(agent.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return agent.to_dict()

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
            "scope": a.get("scope", "private"),
            "created_at": a.get("created_at"),
            "updated_at": a.get("updated_at"),
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

    def save(self, scope: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if scope == "public":
            raise ValueError("Las skills públicas son de solo lectura")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        skill_id = _slug(name)
        d = self.root_dir / scope / skill_id
        d.mkdir(exist_ok=True)
        data = {
            "id": skill_id,
            "name": name,
            "description": str(payload.get("description") or "").strip(),
            "icon": str(payload.get("icon") or "🔧").strip(),
            "category": str(payload.get("category") or "").strip() or None,
            "content": str(payload.get("content") or "").strip(),
        }
        self._write(d / "SKILL.md", data)
        result = self._read(d / "SKILL.md")
        result["scope"] = scope
        return result

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

    def _safe_name(self, filename: str) -> str:
        """Sanitiza el nombre de fichero para prevenir path traversal."""
        name = re.sub(r"[^a-z0-9\-]", "-", filename.lower().removesuffix(".md")).strip("-")
        name = name or "memory"
        return f"{name}.md"

    def list(self) -> List[Dict[str, Any]]:
        items = []
        for p in sorted(self.root_dir.glob("*.md")):
            stat = p.stat()
            items.append({
                "id": p.stem,
                "filename": p.name,
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
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
        return {
            "id": p.stem,
            "filename": p.name,
            "size": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        }

    def delete(self, filename: str) -> bool:
        p = self.root_dir / self._safe_name(filename)
        if not p.exists():
            return False
        p.unlink()
        return True
