"""Cálculo del tipo de propiedad de un recurso respecto al usuario actual."""

from __future__ import annotations

import json
from typing import Any, Dict

from app.errors import APIError


def resource_labels(resource: Dict[str, Any]) -> set[str]:
    """Devuelve las etiquetas aunque SQLite las entregue como JSON serializado."""
    raw = resource.get("labels") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = []
    if not isinstance(raw, list):
        return set()
    return {str(label) for label in raw}


def compute_origin_type(resource: Dict[str, Any]) -> str:
    """Calcula ``owner``, ``linked`` o ``fork`` con una regla canónica."""
    labels = resource_labels(resource)
    if "fork" in labels:
        return "fork"
    if "linked" in labels or resource.get("_shared") is True:
        return "linked"
    return "owner"


def is_linked_resource(resource: Dict[str, Any]) -> bool:
    """Un enlace siempre es de solo lectura, aunque su owner_id sea local."""
    return compute_origin_type(resource) == "linked"


def assert_resource_writable(resource: Dict[str, Any], resource_type: str) -> None:
    """Impide mutaciones sobre referencias ``linked`` desde cualquier cliente."""
    if is_linked_resource(resource):
        raise APIError(
            403,
            "linked_resource_read_only",
            "Los enlaces son de solo lectura",
            extra={"resource": resource_type},
        )
