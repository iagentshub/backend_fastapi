"""Base domain model for an AI agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import ClassVar, List, Optional

from app.config.content_languages import (
    language_codes_from_labels,
    language_label,
)
from app.models.base import BaseResource


def _cron_to_schedule_hint(cron: str) -> Optional[str]:
    """Map a 5-part cron expression to a Claude Code /schedule preset, or None."""
    parts = cron.strip().split()
    if len(parts) != 5:
        return None
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        return None

    def _fmt(h: str) -> str:
        n = int(h)
        return f"{n % 12 or 12}{'am' if n < 12 else 'pm'}"

    if minute == "0" and hour == "*" and dow == "*":
        return "hourly"
    if minute == "0" and hour.isdigit() and dow == "*":
        return f"daily at {_fmt(hour)}"
    if minute == "0" and hour.isdigit() and dow == "1-5":
        return f"weekdays at {_fmt(hour)}"
    if minute == "0" and hour.isdigit() and dow in ("0", "1"):
        return f"weekly at {_fmt(hour)}"
    return None


def _routines_guide(routines: List[dict]) -> List[str]:
    """Build the routines setup guide block for a Claude Code .md export."""
    lines: List[str] = [
        "",
        "---",
        "",
        "> [!NOTE]",
        "> **Routines setup guide** — Run the `/schedule` commands below in Claude Code CLI.",
        "> This section is not part of the agent instructions; remove it after setup.",
        "",
    ]
    for r in routines:
        name = r.get("name") or "Routine"
        lines.append(f"#### {name}")
        if r.get("description"):
            lines.append(r["description"])
            lines.append("")
        tt = r.get("trigger_type", "manual")
        prompt = (r.get("prompt") or "").replace("\n", " ").strip()
        if tt == "cron" and r.get("schedule"):
            hint = _cron_to_schedule_hint(r["schedule"])
            if hint:
                lines += ["```", f"/schedule {hint}, {prompt}", "```"]
            else:
                cron = r["schedule"]
                lines += [
                    f"Cron: `{cron}`",
                    "",
                    "```",
                    f"/schedule daily at 12am, {prompt}",
                    "```",
                    f"Then update the schedule: `/schedule update` → cron `{cron}`",
                ]
        elif tt == "webhook":
            lines.append(
                "**Trigger**: API — configure at <https://claude.ai/code/routines>"
            )
            if prompt:
                lines += ["", f"Prompt: {prompt}"]
        else:
            lines.append("**Trigger**: Manual — invoke on demand with `/schedule run`")
            if prompt:
                lines += ["", f"Prompt: {prompt}"]
        lines.append("")
    return lines


@dataclass(kw_only=True)
class Agent(BaseResource):
    # id, name, description, icon, owner_id, scope, labels, is_active,
    # created_at, updated_at… vienen de BaseResource/BaseEntity.
    resource_type: ClassVar[str] = "agent"

    # ── Discriminator ─────────────────────────────────────────────────────────
    agent_type: str = "generic"

    # ── Display ───────────────────────────────────────────────────────────────
    tags: List[str] = field(default_factory=list)
    language: str = ""

    # ── LLM — portable across platforms ───────────────────────────────────────
    connection_id: Optional[str] = None
    op_connections: List[str] = field(default_factory=list)
    model: str = ""
    system_prompt: str = (
        ""  # maps to: Claude→system, OpenAI→instructions, GitHub→MD body
    )
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: Optional[int] = None  # None = usar preferencia global; 0 = indefinido
    effort_level: Optional[str] = None

    # ── Composition ───────────────────────────────────────────────────────────
    skills: List[str] = field(default_factory=list)
    knowledge: List[str] = field(default_factory=list)
    prompts: List[str] = field(default_factory=list)
    #: IDs de Tool asignadas al agente. Nota: no confundir con `language`
    #: arriba (idioma de export del agente) — `Tool.language` vive en un
    #: dataclass completamente distinto (app.models.tool). Fase 1: sin efecto
    #: en tiempo de ejecución, el modelo aún no puede invocarlas.
    tools: List[str] = field(default_factory=list)
    # Dependencias elegidas expresamente al publicar. ``None`` conserva la
    # semántica de agentes públicos legacy; ``[]`` significa publicar solo el
    # agente. Formato: ``skill:<id>``, ``knowledge:<id>``, etc.
    public_dependencies: Optional[List[str]] = None
    use_memory: bool = False
    memory_file: Optional[str] = None
    routines: List[dict] = field(default_factory=list)

    # ── Semantic labels ───────────────────────────────────────────────────────
    # Redeclarado sobre la base solo para conservar el default ["private"]
    labels: List[str] = field(default_factory=lambda: ["private"])

    # ── Runtime-only (not persisted) ──────────────────────────────────────────
    resolved_skills: List[dict] = field(default_factory=list)

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
        labels = [str(lbl) for lbl in (data.get("labels") or ["private"]) if lbl]
        legacy_language = str(data.get("language") or "").strip().lower()
        legacy_label = language_label(legacy_language)
        if legacy_label and legacy_label not in labels:
            labels.append(legacy_label)
        languages = language_codes_from_labels(labels)
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "").strip(),
            agent_type=str(data.get("agent_type") or "generic"),
            scope=data.get("scope") or "private",  # type: ignore[arg-type]
            description=str(data.get("description") or "").strip(),
            icon=str(data.get("icon") or "").strip(),
            tags=[str(t) for t in (data.get("tags") or []) if t],
            language=languages[0] if languages else "",
            connection_id=str(data.get("connection_id") or "").strip() or None,
            op_connections=[str(c) for c in (data.get("op_connections") or []) if c],
            model=str(data.get("model") or "").strip(),
            system_prompt=str(data.get("system_prompt") or "").strip(),
            temperature=float(data["temperature"])
            if data.get("temperature") is not None
            else 0.7,
            max_tokens=int(data["max_tokens"]) if data.get("max_tokens") else None,
            timeout=int(data["timeout"]) if data.get("timeout") is not None else None,
            effort_level=str(data.get("effort_level") or "").strip() or None,
            skills=[str(s) for s in (data.get("skills") or []) if s],
            knowledge=[str(k) for k in (data.get("knowledge") or []) if k],
            prompts=[str(p) for p in (data.get("prompts") or []) if p],
            tools=[str(t) for t in (data.get("tools") or []) if t],
            public_dependencies=(
                [str(value) for value in data.get("public_dependencies", []) if value]
                if data.get("public_dependencies") is not None
                else None
            ),
            use_memory=bool(data.get("use_memory", False)),
            memory_file=str(data.get("memory_file") or "").strip() or None,
            routines=[r for r in (data.get("routines") or []) if isinstance(r, dict)],
            labels=labels,
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            owner_id=str(data["owner_id"]).strip() or None
            if data.get("owner_id")
            else None,
            created_by=str(data["created_by"]).strip() or None
            if data.get("created_by")
            else None,
            is_active=bool(data.get("is_active", True)),
            deactivated_at=str(data.get("deactivated_at") or "") or None,
            resolved_skills=[
                r for r in (data.get("_resolved_skills") or []) if isinstance(r, dict)
            ],
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Canonical dict written to config.json. Subclasses extend this."""
        return {
            "id": self.id,
            "name": self.name,
            "resource_type": self.resource_type,
            "agent_type": self.agent_type,
            "scope": self.scope,
            "is_active": self.is_active,
            "deactivated_at": self.deactivated_at,
            "description": self.description,
            "icon": self.icon,
            "tags": self.tags,
            "labels": self.labels,
            "language": self.language,
            "connection_id": self.connection_id,
            "op_connections": self.op_connections,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "effort_level": self.effort_level,
            "skills": self.skills,
            "knowledge": self.knowledge,
            "prompts": self.prompts,
            "tools": self.tools,
            "public_dependencies": self.public_dependencies,
            "use_memory": self.use_memory,
            "memory_file": self.memory_file,
            "routines": self.routines,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "owner_id": self.owner_id,
            "created_by": self.created_by,
        }

    # ── Export ────────────────────────────────────────────────────────────────

    def export(self, fmt: str) -> tuple[str, str, str]:
        """Return (content, media_type, filename).
        Base class provides generic exports for all three formats.
        Subclasses override to add platform-specific fields.
        """
        if fmt == "claude":
            fm: List[str] = ["---", f"name: {self.name}"]
            desc = (self.description or self.name).replace(chr(34), chr(92) + chr(34))
            fm.append(f'description: "{desc}"')
            if self.model:
                fm.append(f"model: {self.model}")
            fm.append("---")
            body: List[str] = [""]
            if self.system_prompt:
                body.append(self.system_prompt)
            if self.routines:
                body += _routines_guide(self.routines)
            content = "\n".join(fm + body).strip() + "\n"
            return content, "text/markdown; charset=utf-8", f"{self.id}.md"

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
            return (
                json.dumps(payload, indent=2, ensure_ascii=False),
                "application/json",
                f"{self.id}-openai.json",
            )

        if fmt == "github":
            desc = (self.description or self.name).replace(chr(34), chr(92) + chr(34))
            fm: List[str] = [
                "---",
                f"name: {self.name}",
                f'description: "{desc}"',
                "---",
            ]
            body: List[str] = [""]
            if self.system_prompt:
                body.append(self.system_prompt)
            content = "\n".join(fm + body).strip() + "\n"
            return content, "text/markdown; charset=utf-8", f"{self.id}.md"

        if fmt == "mcp":
            import re

            def _to_fn_name(s: str) -> str:
                slug = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
                return slug or "tool"

            lines: List[str] = [
                "from mcp.server.fastmcp import FastMCP",
                "",
                f'mcp = FastMCP("{self.name}")',
            ]
            if self.resolved_skills:
                for sk in self.resolved_skills:
                    fn = _to_fn_name(sk.get("name") or sk.get("id") or "tool")
                    desc = (sk.get("description") or "").replace('"', '\\"')
                    lines += [
                        "",
                        "",
                        "@mcp.tool()",
                        f"def {fn}(prompt: str) -> str:",
                        f'    """{desc}"""',
                        "    raise NotImplementedError",
                    ]
            else:
                agent_fn = _to_fn_name(self.name)
                desc = (self.description or self.name).replace('"', '\\"')
                lines += [
                    "",
                    "",
                    "@mcp.tool()",
                    f"def {agent_fn}(prompt: str) -> str:",
                    f'    """{desc}"""',
                    "    raise NotImplementedError",
                ]
            lines += ["", "", 'if __name__ == "__main__":', "    mcp.run()"]
            content = "\n".join(lines) + "\n"
            return content, "text/x-python", f"{self.id}-mcp.py"

        raise NotImplementedError(
            f"Export format {fmt!r} not supported for agent_type={self.agent_type!r}"
        )
