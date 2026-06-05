"""OpenAI agent model."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from app.models.agent import Agent


@dataclass
class OpenAIAgent(Agent):
    agent_type: str = "openai"

    # Structured output
    response_format: str = "text"          # "text" | "json_object"
    json_schema: Optional[dict] = None     # used when response_format == "json_schema"

    # Sampling
    top_p: Optional[float] = None
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    seed: Optional[int] = None

    # Tool use
    tool_choice: str = "auto"              # "none" | "auto" | "required"

    @classmethod
    def _build(cls, data: dict) -> "OpenAIAgent":
        base = Agent._build.__func__(cls, data)  # type: ignore[attr-defined]
        base.response_format = str(data.get("response_format") or "text")
        base.json_schema = data.get("json_schema") or None
        base.top_p = float(data["top_p"]) if data.get("top_p") is not None else None
        base.frequency_penalty = float(data.get("frequency_penalty") or 0.0)
        base.presence_penalty = float(data.get("presence_penalty") or 0.0)
        base.seed = int(data["seed"]) if data.get("seed") is not None else None
        base.tool_choice = str(data.get("tool_choice") or "auto")
        return base

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "response_format": self.response_format,
            "json_schema": self.json_schema,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "seed": self.seed,
            "tool_choice": self.tool_choice,
        })
        return d

    def export(self, fmt: str = "openai") -> tuple[str, str, str]:
        payload: dict = {
            "model": self.model or "gpt-4o",
            "name": self.name,
            "description": self.description,
            "instructions": self.system_prompt,
            "temperature": self.temperature,
            "response_format": {"type": self.response_format},
        }
        if self.max_tokens:
            payload["max_response_output_tokens"] = self.max_tokens
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        if self.frequency_penalty:
            payload["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty:
            payload["presence_penalty"] = self.presence_penalty
        if self.seed is not None:
            payload["seed"] = self.seed
        payload["tool_choice"] = self.tool_choice
        if self.json_schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": self.json_schema}
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        return content, "application/json", f"{self.id}-openai.json"
