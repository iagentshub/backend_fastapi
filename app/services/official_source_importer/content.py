"""Leer un fichero del repositorio: frontmatter, mapas y qué es un agente."""

from __future__ import annotations

import json
import tomllib
from pathlib import PurePosixPath
from typing import Any, Dict

import yaml


def _frontmatter(text: str) -> Dict[str, Any]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        value = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


def _structured_mapping(content: str, suffix: str) -> Dict[str, Any]:
    try:
        if suffix == ".json":
            value = json.loads(content)
        elif suffix == ".toml":
            value = tomllib.loads(content)
        else:
            value = yaml.safe_load(content)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_agent_definition(
    path: PurePosixPath,
    content: str,
    meta: Dict[str, Any],
    *,
    declared: bool,
) -> bool:
    if declared:
        return True
    if path.suffix.lower() == ".md":
        body = (
            content.split("---", 2)[-1].strip()
            if content.startswith("---")
            else content.strip()
        )
        return bool(meta.get("name") and body)
    structured = _structured_mapping(content, path.suffix.lower())
    has_identity = bool(structured.get("name") or structured.get("id"))
    has_instructions = any(
        structured.get(key)
        for key in ("system_prompt", "instructions", "prompt", "role", "content")
    )
    return has_identity and has_instructions
