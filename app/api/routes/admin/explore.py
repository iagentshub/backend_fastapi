"""Inventario administrativo unificado y grafo de relaciones entre recursos."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, Query

from app.api.routes.admin._router import admin_router
from app.api.routes.admin.resources import (
    _workflows,
    admin_list_agents,
    admin_list_connections,
    admin_list_groups,
    admin_list_knowledge,
    admin_list_memory,
    admin_list_prompts,
    admin_list_skills,
    admin_list_tools,
    admin_list_workflows,
)
from app.api.routes.admin.users import admin_list_users
from app.api.routes.auth import require_admin
from app.errors import APIError
from app.storage.db import open_db

_ADMIN_EXPLORE_TYPES = (
    "user",
    "group",
    "agent",
    "connection",
    "knowledge",
    "workflow",
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
    return {
        "items": items[offset : offset + limit],
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


@admin_router.get("/resources/{resource_type}/{resource_id}/graph")
async def admin_resource_graph(
    resource_type: str,
    resource_id: str,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Devuelve el vecindario relacional inmediato de un recurso de admin."""
    if resource_type not in _ADMIN_EXPLORE_TYPES:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de recurso no válido",
            extra={"field": "resource_type"},
        )

    inventory = await _admin_inventory()
    workflows_full = await _workflows.list_all()
    workflows_by_id = {str(item.get("id")): item for item in workflows_full}
    users_by_id = {str(item.get("id")): item for item in inventory["user"]}
    users_by_username = {str(item.get("username")): item for item in inventory["user"]}
    groups_by_id = {str(item.get("id")): item for item in inventory["group"]}
    resources_by_type = {
        kind: {str(item.get("id")): item for item in values}
        for kind, values in inventory.items()
        if kind not in ("user", "group")
    }

    if resource_type == "user":
        root = users_by_id.get(resource_id) or users_by_username.get(resource_id)
    elif resource_type == "group":
        root = groups_by_id.get(resource_id)
    else:
        root = resources_by_type[resource_type].get(resource_id)
    if not root:
        raise APIError(
            404,
            "not_found",
            "Recurso no encontrado",
            extra={"resource": resource_type},
        )

    canonical_resource_id = str(root.get("id") or resource_id)
    nodes: dict[str, dict[str, str]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_node(kind: str, item_id: str, label: str, description: str = "") -> str:
        node = _graph_node(kind, item_id, label, description)
        nodes[node["id"]] = node
        return node["id"]

    def add_edge(
        source: str, target: str, relation: str, *, dashed: bool = False
    ) -> None:
        edges[(source, target, relation)] = {
            "source_id": source,
            "target_id": target,
            "relation": relation,
            "dashed": dashed,
        }

    def resource_label(kind: str, item: dict[str, Any]) -> str:
        return str(
            item.get("name")
            or item.get("title")
            or item.get("username")
            or item.get("id")
            or kind
        )

    root_id = add_node(
        resource_type,
        canonical_resource_id,
        resource_label(resource_type, root),
        str(root.get("description") or root.get("email") or ""),
    )

    def connect_owner(kind: str, item: dict[str, Any]) -> None:
        owner_id = str(item.get("owner_id") or "")
        if not owner_id:
            return
        if owner_id in users_by_id:
            owner = users_by_id[owner_id]
            owner_node = add_node("user", owner_id, resource_label("user", owner))
        elif owner_id in groups_by_id:
            owner = groups_by_id[owner_id]
            owner_node = add_node("group", owner_id, resource_label("group", owner))
        else:
            return
        item_node = add_node(kind, str(item["id"]), resource_label(kind, item))
        add_edge(owner_node, item_node, "owns")

    if resource_type in resources_by_type:
        connect_owner(resource_type, root)

    def wire_agent_uses(agent: dict[str, Any], agent_node: str) -> None:
        """Añade las aristas "usa" de un agente (conexión/knowledge/skill/
        memoria) — reutilizable tanto cuando el agente es la raíz del grafo
        como cuando aparece como hijo de su usuario/grupo propietario, para
        que esa vista también muestre qué usa cada agente y no solo qué
        posee el usuario en plano."""
        connection_ids = [str(agent.get("connection_id") or "")]
        connection_ids.extend(
            str(value).split("::", 1)[0]
            for value in (agent.get("op_connections") or [])
        )
        for connection_id in {value for value in connection_ids if value}:
            connection = resources_by_type["connection"].get(connection_id)
            if connection:
                connection_node = add_node(
                    "connection",
                    connection_id,
                    resource_label("connection", connection),
                )
                add_edge(agent_node, connection_node, "uses")
        for knowledge_id in agent.get("knowledge") or []:
            knowledge = resources_by_type["knowledge"].get(str(knowledge_id))
            if knowledge:
                knowledge_node = add_node(
                    "knowledge",
                    str(knowledge_id),
                    resource_label("knowledge", knowledge),
                )
                add_edge(agent_node, knowledge_node, "uses")
        for skill_id in agent.get("skills") or []:
            skill = resources_by_type["skill"].get(str(skill_id))
            skill_node = add_node(
                "skill",
                str(skill_id),
                resource_label("skill", skill) if skill else str(skill_id),
            )
            add_edge(agent_node, skill_node, "uses")
        for prompt_id in agent.get("prompts") or []:
            prompt = resources_by_type["prompt"].get(str(prompt_id))
            prompt_node = add_node(
                "prompt",
                str(prompt_id),
                resource_label("prompt", prompt) if prompt else str(prompt_id),
            )
            add_edge(agent_node, prompt_node, "uses")
        for tool_id in agent.get("tools") or []:
            tool = resources_by_type["tool"].get(str(tool_id))
            tool_node = add_node(
                "tool",
                str(tool_id),
                resource_label("tool", tool) if tool else str(tool_id),
            )
            add_edge(agent_node, tool_node, "uses")
        memory_file = str(agent.get("memory_file") or "")
        if agent.get("use_memory") and memory_file:
            # El id de memoria es compuesto ("owner_id::filename", ver
            # admin_list_memory) porque el nombre solo es único por dueño.
            memory_key = f"{str(agent.get('owner_id') or '')}::{memory_file}"
            memory = resources_by_type["memory"].get(memory_key)
            if memory:
                memory_node = add_node("memory", memory_key, memory_file)
                add_edge(agent_node, memory_node, "uses")

    async with open_db() as conn:
        member_rows = await conn.fetchall(
            "SELECT group_id, username, role FROM group_members"
        )
        share_rows = await conn.fetchall(
            "SELECT group_id, resource_type, resource_id FROM resource_group_shares"
        )

    if resource_type == "user":
        username = str(root.get("username") or "")
        user_id = str(root.get("id") or "")
        for row in member_rows:
            if str(row["username"]) != username:
                continue
            group = groups_by_id.get(str(row["group_id"]))
            if group:
                group_node = add_node(
                    "group", str(group["id"]), resource_label("group", group)
                )
                add_edge(root_id, group_node, f"member:{row['role']}")
        for kind, by_id in resources_by_type.items():
            for item in by_id.values():
                if str(item.get("owner_id") or "") == user_id:
                    item_node = add_node(
                        kind, str(item["id"]), resource_label(kind, item)
                    )
                    add_edge(root_id, item_node, "owns")
                    if kind == "agent":
                        wire_agent_uses(item, item_node)

    if resource_type == "group":
        for row in member_rows:
            if str(row["group_id"]) != canonical_resource_id:
                continue
            user = users_by_username.get(str(row["username"]))
            if user:
                user_node = add_node(
                    "user", str(user["id"]), resource_label("user", user)
                )
                add_edge(user_node, root_id, f"member:{row['role']}")
        for kind, by_id in resources_by_type.items():
            for item in by_id.values():
                if str(item.get("owner_id") or "") == canonical_resource_id:
                    item_node = add_node(
                        kind, str(item["id"]), resource_label(kind, item)
                    )
                    add_edge(root_id, item_node, "owns")
                    if kind == "agent":
                        wire_agent_uses(item, item_node)

    for row in share_rows:
        kind = str(row["resource_type"])
        item_id = str(row["resource_id"])
        group_id = str(row["group_id"])
        if kind not in resources_by_type or item_id not in resources_by_type[kind]:
            continue
        if not (
            (resource_type == "group" and canonical_resource_id == group_id)
            or (resource_type == kind and canonical_resource_id == item_id)
        ):
            continue
        group = groups_by_id.get(group_id)
        item = resources_by_type[kind][item_id]
        if group:
            group_node = add_node("group", group_id, resource_label("group", group))
            item_node = add_node(kind, item_id, resource_label(kind, item))
            add_edge(group_node, item_node, "shared", dashed=True)

    agents = resources_by_type["agent"]
    if resource_type == "agent":
        wire_agent_uses(root, root_id)

    if resource_type == "memory":
        memory_owner_id, _, memory_filename = canonical_resource_id.partition("::")
        for agent in agents.values():
            if (
                str(agent.get("owner_id") or "") == memory_owner_id
                and agent.get("use_memory")
                and str(agent.get("memory_file") or "") == memory_filename
            ):
                agent_node = add_node(
                    "agent", str(agent["id"]), resource_label("agent", agent)
                )
                add_edge(agent_node, root_id, "uses")

    if resource_type in ("connection", "knowledge", "skill", "prompt", "tool"):
        field = (
            "connection_id"
            if resource_type == "connection"
            else "knowledge"
            if resource_type == "knowledge"
            else "prompts"
            if resource_type == "prompt"
            else "tools"
            if resource_type == "tool"
            else "skills"
        )
        for agent in agents.values():
            if field == "connection_id":
                operation_connections = {
                    str(value).split("::", 1)[0]
                    for value in (agent.get("op_connections") or [])
                }
                related = (
                    str(agent.get(field) or "") == canonical_resource_id
                    or canonical_resource_id in operation_connections
                )
            else:
                related = canonical_resource_id in {
                    str(value) for value in (agent.get(field) or [])
                }
            if related:
                agent_node = add_node(
                    "agent", str(agent["id"]), resource_label("agent", agent)
                )
                add_edge(agent_node, root_id, "uses")

    for workflow_id, workflow in workflows_by_id.items():
        agent_ids = {
            str(node.get("agent_id") or "")
            for node in (workflow.get("definition") or {}).get("nodes", [])
        } - {""}
        if resource_type == "workflow" and workflow_id == canonical_resource_id:
            for agent_id in agent_ids:
                agent = agents.get(agent_id)
                agent_node = add_node(
                    "agent",
                    agent_id,
                    resource_label("agent", agent) if agent else agent_id,
                )
                add_edge(root_id, agent_node, "orchestrates")
        elif resource_type == "agent" and canonical_resource_id in agent_ids:
            workflow_item = resources_by_type["workflow"].get(workflow_id, workflow)
            workflow_node = add_node(
                "workflow", workflow_id, resource_label("workflow", workflow_item)
            )
            add_edge(workflow_node, root_id, "orchestrates")

    return {
        "root_id": root_id,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }
