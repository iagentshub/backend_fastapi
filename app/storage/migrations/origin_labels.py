"""Pure helpers for the one-off resource-origin label migration."""

from __future__ import annotations

import json
from typing import Any


def normalize_labels(raw: Any, *, origin: str, drop_production: bool = False) -> str:
    try:
        parsed = json.loads(raw or "[]") if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        parsed = []
    labels = [str(label) for label in parsed] if isinstance(parsed, list) else []
    excluded = {"official", "community"}
    if drop_production:
        excluded.add("production")
    labels = list(dict.fromkeys(label for label in labels if label not in excluded))
    insert_at = next(
        (index + 1 for index, label in enumerate(labels) if label in {"private", "public"}),
        0,
    )
    labels.insert(insert_at, origin)
    return json.dumps(labels, ensure_ascii=False, separators=(",", ":"))


def normalize_resource_data(raw: Any) -> str:
    try:
        data = json.loads(raw or "{}") if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    current = data.get("labels") or [data.get("scope") or "private"]
    requested_origin = "official" if "official" in current else "community"
    data["labels"] = json.loads(normalize_labels(current, origin=requested_origin))
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
