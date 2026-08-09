"""Presentación localizada de agentes, independiente de la capa HTTP."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.errors import APIError
from app.utils import flog

_FIELDS = ("name", "description", "system_prompt")
_CACHE: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}


def agent_name_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]", "-", name.lower().strip())
    return re.sub(r"-{2,}", "-", slug).strip("-") or "agent"


def validate_agent_scope(scope: str) -> None:
    if scope not in {"public", "private", "all"}:
        raise APIError(
            400, "invalid_field", "Scope no válido", extra={"field": "scope"}
        )


def _overrides(
    agents_dir: Path, scope: str, agent_id: str, locale: str
) -> dict[str, Any]:
    path = agents_dir / scope / agent_id / f"locale.{locale}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    key = (str(agents_dir), scope, agent_id, locale)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("el fichero de locale no contiene un objeto JSON")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        flog.warning(f"[agents] Locale omitido {path}: {exc}")
        data = {}
    _CACHE[key] = (mtime, data)
    return data


def apply_agent_locale(
    agent: dict[str, Any], locale: str, agents_dir: Path
) -> dict[str, Any]:
    if not agent:
        return agent
    overrides = _overrides(
        agents_dir, agent.get("scope", "public"), agent.get("id", ""), locale
    )
    if not overrides and locale != "es":
        overrides = _overrides(
            agents_dir, agent.get("scope", "public"), agent.get("id", ""), "es"
        )
    for field in _FIELDS:
        if field in overrides:
            agent = {**agent, field: overrides[field]}
    return agent
