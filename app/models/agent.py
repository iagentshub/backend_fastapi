"""Base domain model for an AI agent."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Literal, Optional


@dataclass
class Agent:
    # ── Minimum required ──────────────────────────────────────────────────────
    id: str
    name: str

    # ── Discriminator ─────────────────────────────────────────────────────────
    agent_type: str = "generic"
    scope: Literal["public", "private"] = "private"

    # ── Display ───────────────────────────────────────────────────────────────
    description: str = ""
    icon: str = ""
    tags: List[str] = field(default_factory=list)
    language: str = ""

    # ── LLM — portable across platforms ───────────────────────────────────────
    connection_id: Optional[str] = None
    model: str = ""
    system_prompt: str = ""   # maps to: Claude→system, OpenAI→instructions, GitHub→MD body
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: int = 120        # consumed by chat.py but was not persisted before

    # ── Composition ───────────────────────────────────────────────────────────
    skills: List[str] = field(default_factory=list)
    use_memory: bool = False
    memory_file: Optional[str] = None
    routines: List[dict] = field(default_factory=list)

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_at: str = ""
    updated_at: str = ""

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict) -> "Agent":
        """Return the correct subclass based on agent_type."""
        from app.models.claude_agent import ClaudeAgent
        from app.models.github_agent import GitHubAgent
        from app.models.openai_agent import OpenAIAgent

        subclass = {
            "claude": ClaudeAgent,
            "openai": OpenAIAgent,
            "github": GitHubAgent,
        }.get(str(data.get("agent_type") or "generic"), cls)
        return subclass._build(data)

    @classmethod
    def _build(cls, data: dict) -> "Agent":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "").strip(),
            agent_type=str(data.get("agent_type") or "generic"),
            scope=data.get("scope") or "private",  # type: ignore[arg-type]
            description=str(data.get("description") or "").strip(),
            icon=str(data.get("icon") or "").strip(),
            tags=[str(t) for t in (data.get("tags") or []) if t],
            language=str(data.get("language") or "").strip(),
            connection_id=str(data.get("connection_id") or "").strip() or None,
            model=str(data.get("model") or "").strip(),
            system_prompt=str(data.get("system_prompt") or "").strip(),
            temperature=float(data["temperature"]) if data.get("temperature") is not None else 0.7,
            max_tokens=int(data["max_tokens"]) if data.get("max_tokens") else None,
            timeout=int(data["timeout"]) if data.get("timeout") is not None else 120,
            skills=[str(s) for s in (data.get("skills") or []) if s],
            use_memory=bool(data.get("use_memory", False)),
            memory_file=str(data.get("memory_file") or "").strip() or None,
            routines=[r for r in (data.get("routines") or []) if isinstance(r, dict)],
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Canonical dict written to config.json. Subclasses extend this."""
        return {
            "id": self.id,
            "name": self.name,
            "agent_type": self.agent_type,
            "scope": self.scope,
            "description": self.description,
            "icon": self.icon,
            "tags": self.tags,
            "language": self.language,
            "connection_id": self.connection_id,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "skills": self.skills,
            "use_memory": self.use_memory,
            "memory_file": self.memory_file,
            "routines": self.routines,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ── Export ────────────────────────────────────────────────────────────────

    def export(self, fmt: str) -> tuple[str, str, str]:
        """Return (content, media_type, filename).
        Base class provides generic exports for all three formats.
        Subclasses override to add platform-specific fields.
        """
        if fmt == "claude":
            payload: dict = {
                "name": self.name,
                "description": self.description,
                "model": self.model or "claude-sonnet-4-6",
                "system_prompt": self.system_prompt,
                "temperature": self.temperature,
            }
            if self.max_tokens:
                payload["max_tokens"] = self.max_tokens
            if self.routines:
                payload["routines"] = self.routines
            return json.dumps(payload, indent=2, ensure_ascii=False), "application/json", f"{self.id}-claude.json"

        if fmt == "openai":
            payload = {
                "model": self.model or "gpt-4o",
                "name": self.name,
                "description": self.description,
                "instructions": self.system_prompt,
                "temperature": self.temperature,
            }
            if self.max_tokens:
                payload["max_response_output_tokens"] = self.max_tokens
            return json.dumps(payload, indent=2, ensure_ascii=False), "application/json", f"{self.id}-openai.json"

        if fmt == "github":
            parts = [f"# {self.name}"]
            if self.description:
                parts += ["", self.description]
            if self.system_prompt:
                parts += ["", self.system_prompt]
            if self.routines:
                parts += ["", "## Routines"]
                for r in self.routines:
                    parts.append(f"\n### {r.get('name', 'Routine')}")
                    tt = r.get("trigger_type", "manual")
                    if tt == "cron" and r.get("schedule"):
                        parts.append(f"- **Trigger**: cron `{r['schedule']}`")
                    else:
                        parts.append(f"- **Trigger**: {tt}")
                    if r.get("description"):
                        parts.append(f"- **Description**: {r['description']}")
                    if r.get("prompt"):
                        parts.append(f"- **Prompt**: {r['prompt']}")
            content = "\n".join(parts).strip()
            return content, "text/markdown; charset=utf-8", "copilot-instructions.md"

        raise NotImplementedError(f"Export format {fmt!r} not supported for agent_type={self.agent_type!r}")
