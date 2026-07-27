"""Cálculo del origen (`origin_type`) de un recurso respecto al usuario actual."""

from __future__ import annotations

from typing import Any, Dict


def compute_origin_type(resource: Dict[str, Any]) -> str:
    """"linked" si el recurso llegó vía workspace share (`_shared=True`), si no "owner"."""
    return "linked" if resource.get("_shared") is True else "owner"
