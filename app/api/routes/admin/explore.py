"""Inventario administrativo unificado y grafo de relaciones entre recursos."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, Query

import app.services.resource_relations as _relations
from app.api.routes.admin._router import admin_router
from app.api.routes.admin.resources import (
    admin_list_agents,
    admin_list_connections,
    admin_list_groups,
    admin_list_knowledge,
    admin_list_llm_orchestrations,
    admin_list_memory,
    admin_list_prompts,
    admin_list_skills,
    admin_list_tools,
    admin_list_workflows,
)
from app.api.routes.admin.users import admin_list_users
from app.api.routes.auth import require_admin
from app.errors import APIError
from app.pagination.materialized import paginate_materialized
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.official_source_storage import OfficialSourceStorage

_official_sources = OfficialSourceStorage()
_knowledge_packs = KnowledgePackStorage()

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


def _explore_search_text(resource_type: str, item: dict[str, Any]) -> str:
    fields = {
        "user": ("username", "email", "display_name"),
        "group": ("name", "created_by_username"),
        "agent": ("name", "id", "owner_username", "description"),
        "connection": ("name", "id", "owner_username", "type"),
        "knowledge": ("title", "id", "owner_username", "type"),
        "workflow": ("name", "id", "owner_username", "description"),
        "llm_orchestration": ("name", "id", "owner_username", "description", "mode"),
        "skill": ("name", "id", "owner_username", "category"),
        "prompt": ("name", "id", "owner_username", "alias"),
        "tool": ("name", "id", "owner_username", "language"),
        "memory": ("filename", "id", "owner_username"),
    }[resource_type]
    return " ".join(str(item.get(field) or "") for field in fields).lower()


async def _admin_inventory() -> dict[str, list[dict[str, Any]]]:
    (
        users,
        groups,
        agents,
        connections,
        knowledge,
        workflows,
        llm_orchestrations,
        skills,
        prompts,
        tools,
        memory,
    ) = await asyncio.gather(
        admin_list_users(_=""),
        admin_list_groups(_=""),
        admin_list_agents(_=""),
        admin_list_connections(_=""),
        admin_list_knowledge(_=""),
        admin_list_workflows(_=""),
        admin_list_llm_orchestrations(_=""),
        admin_list_skills(_=""),
        admin_list_prompts(_=""),
        admin_list_tools(_=""),
        admin_list_memory(_=""),
    )
    return {
        "user": users,
        "group": groups,
        "agent": agents,
        "connection": connections,
        "knowledge": knowledge,
        "workflow": workflows,
        "llm_orchestration": llm_orchestrations,
        "skill": skills,
        "prompt": prompts,
        "tool": tools,
        "memory": memory,
    }


@admin_router.get("/explore")
async def admin_explore(
    resource_types: list[str] | None = Query(None, alias="type"),
    q: str | None = None,
    owner: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Inventario administrativo unificado con discriminador por tipo."""
    requested = set(resource_types or _ADMIN_EXPLORE_TYPES)
    invalid = requested.difference(_ADMIN_EXPLORE_TYPES)
    if invalid:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de recurso no válido",
            extra={"field": "type"},
        )

    inventory = await _admin_inventory()
    query = (q or "").strip().lower()
    owner_filter = (owner or "").strip().lower()
    counts = {resource_type: len(values) for resource_type, values in inventory.items()}
    items: list[dict[str, Any]] = []
    for resource_type in _ADMIN_EXPLORE_TYPES:
        if resource_type not in requested:
            continue
        for raw in inventory[resource_type]:
            if query and query not in _explore_search_text(resource_type, raw):
                continue
            item_owner = str(
                raw.get("owner_username")
                or raw.get("created_by_username")
                or raw.get("username")
                or ""
            ).lower()
            if owner_filter and item_owner != owner_filter:
                continue
            item = dict(raw)
            item["resource_type"] = resource_type
            items.append(item)

    def sort_key(item: dict[str, Any]) -> str:
        return str(item.get("updated_at") or item.get("created_at") or "")

    items.sort(key=sort_key, reverse=True)
    page_items = paginate_materialized(items, limit=limit, offset=offset)
    return {
        "items": page_items,
        "total": len(items),
        "counts": counts,
        "limit": limit,
        "offset": offset,
    }


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
