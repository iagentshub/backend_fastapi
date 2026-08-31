"""Inventario administrativo unificado y grafo de relaciones entre recursos."""

from __future__ import annotations

from typing import Any

from fastapi import Depends

import app.services.resource_relations as _relations
from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.errors import APIError

_ADMIN_EXPLORE_TYPES = (
    "user",
    "group",
    "agent",
    "connection",
    "knowledge",
    "workflow",
    "llm_orchestration",
    "skill",
    "prompt",
    "tool",
    "memory",
)


def _graph_node(
    resource_type: str,
    resource_id: str,
    label: str,
    description: str = "",
) -> dict[str, str]:
    return {
        "id": f"{resource_type}:{resource_id}",
        "resource_id": resource_id,
        "label": label or resource_id,
        "type": resource_type,
        "description": description,
    }


def _validar_tipo(resource_type: str) -> None:
    if resource_type not in _ADMIN_EXPLORE_TYPES:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de recurso no válido",
            extra={"field": "resource_type"},
        )


@admin_router.get("/resources/{resource_type}/{resource_id}/relations")
async def admin_resource_relations(
    resource_type: str,
    resource_id: str,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Vecindario relacional inmediato de un recurso, en hechos planos."""
    _validar_tipo(resource_type)
    relations = await _relations.admin_relations(resource_type, resource_id)
    if relations is None:
        raise APIError(
            404,
            "not_found",
            "Recurso no encontrado",
            extra={"resource": resource_type},
        )
    return relations
