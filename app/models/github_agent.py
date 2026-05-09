"""GitHub Copilot agent model."""
from __future__ import annotations

from dataclasses import dataclass

from app.models.agent import Agent


@dataclass
class GitHubAgent(Agent):
    agent_type: str = "github"

    copilot_topic: str = ""           # category hint for Copilot
    include_repo_context: bool = False

    @classmethod
    def _build(cls, data: dict) -> "GitHubAgent":
        base = Agent._build.__func__(cls, data)  # type: ignore[attr-defined]
        base.copilot_topic = str(data.get("copilot_topic") or "").strip()
        base.include_repo_context = bool(data.get("include_repo_context", False))
        return base

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "copilot_topic": self.copilot_topic,
            "include_repo_context": self.include_repo_context,
        })
        return d

    def export(self, fmt: str = "github") -> tuple[str, str, str]:
        parts = [f"# {self.name}"]
        if self.description:
            parts += ["", self.description]
        if self.copilot_topic:
            parts += ["", f"**Topic:** {self.copilot_topic}"]
        if self.system_prompt:
            parts += ["", self.system_prompt]
        content = "\n".join(parts).strip()
        return content, "text/markdown; charset=utf-8", "copilot-instructions.md"
