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
        desc = (self.description or self.copilot_topic or self.name).replace('"', '\\"')
        fm = ["---", f"name: {self.name}", f'description: "{desc}"']
        if self.copilot_topic:
            fm.append(f"topic: {self.copilot_topic}")
        fm.append("---")
        body: list[str] = [""]
        if self.system_prompt:
            body.append(self.system_prompt)
        content = "\n".join(fm + body).strip() + "\n"
        return content, "text/markdown; charset=utf-8", f"{self.id}.md"
