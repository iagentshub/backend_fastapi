"""Tipos admitidos, campos de dependencia y dos helpers de lectura."""


from __future__ import annotations

import json
from typing import Any, Iterable

_SUPPORTED_TYPES = frozenset(
    {"skill", "knowledge", "prompt", "tool", "memory", "agent", "workflow"}
)

_DEPENDENCY_FIELDS = {
    "skills": "skill",
    "knowledge": "knowledge",
    "prompts": "prompt",
    "tools": "tool",
}

def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return (
        [str(item) for item in decoded if str(item)]
        if isinstance(decoded, list)
        else []
    )

def _linked_labels(labels: Iterable[str]) -> list[str]:
    result = [
        str(label)
        for label in labels
        if label and label not in {"fork", "linked", "public", "private"}
    ]
    return list(dict.fromkeys(["private", *result, "linked"]))
