"""Anthropic / Claude agent model."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.models.agent import Agent


@dataclass
class ClaudeAgent(Agent):
    agent_type: str = "claude"

    # Extended thinking (claude-3-7-sonnet and above)
    extended_thinking: bool = False
    thinking_budget_tokens: int = 10_000

    # Prompt caching — reduces cost on long system prompts
    cache_control: bool = False

    # Sampling
    top_k: Optional[int] = None
    stop_sequences: List[str] = field(default_factory=list)

    # Anthropic beta features (e.g. "interleaved-thinking-2025-05-14")
    anthropic_betas: List[str] = field(default_factory=list)

    @classmethod
    def _build(cls, data: dict) -> "ClaudeAgent":
        base = Agent._build.__func__(cls, data)  # type: ignore[attr-defined]
        base.extended_thinking = bool(data.get("extended_thinking", False))
        base.thinking_budget_tokens = int(data.get("thinking_budget_tokens") or 10_000)
        base.cache_control = bool(data.get("cache_control", False))
        base.top_k = int(data["top_k"]) if data.get("top_k") is not None else None
        base.stop_sequences = [str(s) for s in (data.get("stop_sequences") or []) if s]
        base.anthropic_betas = [str(b) for b in (data.get("anthropic_betas") or []) if b]
        return base

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "extended_thinking": self.extended_thinking,
            "thinking_budget_tokens": self.thinking_budget_tokens,
            "cache_control": self.cache_control,
            "top_k": self.top_k,
            "stop_sequences": self.stop_sequences,
            "anthropic_betas": self.anthropic_betas,
        })
        return d

