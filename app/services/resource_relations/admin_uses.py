"""Qué usa un agente y quién usa a un recurso, visto desde Admin."""


from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.resource_relations._shared import (
    _knowledge_pack_of,
    _pack_member_items,
    admin_labels,
    item,
)
from app.sql import sql
from app.storage.db import open_db


async def _admin_agent_uses(
    agent: Dict[str, Any], *, via: Optional[tuple[str, str]]
) -> List[Dict[str, Any]]:
    """Lo que usa un agente, con los nombres resueltos por lote."""
    from app.models.llm_orchestration import orchestration_id_from_connection

    items: List[Dict[str, Any]] = []

    connection_ids = {str(agent.get("connection_id") or "")}
    connection_ids.update(
        str(value).split("::", 1)[0] for value in (agent.get("op_connections") or [])
    )
    connection_ids.discard("")
    orchestration_ids = {
        orchestration_id_from_connection(value) for value in connection_ids
    }
    orchestration_ids.discard(None)
    plain_connections = [
        value
        for value in connection_ids
        if not orchestration_id_from_connection(value)
    ]

    labels = {
        "connection": await admin_labels("connection", plain_connections),
        "llm_orchestration": await admin_labels(
            "llm_orchestration", [str(value) for value in orchestration_ids]
        ),
        "knowledge_pack": await admin_labels(
            "knowledge_pack",
            [str(value) for value in (agent.get("knowledge_packs") or [])],
        ),
        "knowledge": await admin_labels(
            "knowledge", [str(value) for value in (agent.get("knowledge") or [])]
        ),
        "skill": await admin_labels(
            "skill", [str(value) for value in (agent.get("skills") or [])]
        ),
        "prompt": await admin_labels(
            "prompt", [str(value) for value in (agent.get("prompts") or [])]
        ),
        "tool": await admin_labels(
            "tool", [str(value) for value in (agent.get("tools") or [])]
        ),
    }

    for orchestration_id in sorted(str(value) for value in orchestration_ids):
        label = labels["llm_orchestration"].get(orchestration_id)
        if label:
            items.append(
                item(
                    "llm_orchestration",
                    orchestration_id,
                    label,
                    relation="uses",
                    via=via,
                )
            )
    for connection_id in plain_connections:
        label = labels["connection"].get(connection_id)
        if label:
            items.append(
                item("connection", connection_id, label, relation="uses", via=via)
            )

    for pack_id in (str(value) for value in (agent.get("knowledge_packs") or [])):
        label = labels["knowledge_pack"].get(pack_id)
        if not label:
            continue
        items.append(
            item("knowledge_pack", pack_id, label, relation="uses", via=via)
        )
        items.extend(
            await _pack_member_items(pack_id, via=("knowledge_pack", pack_id))
        )

    for knowledge_id in (str(value) for value in (agent.get("knowledge") or [])):
        label = labels["knowledge"].get(knowledge_id)
        if not label:
            continue
        pack_id, relative_path = await _knowledge_pack_of(knowledge_id)
        if pack_id:
            pack_labels = await admin_labels("knowledge_pack", [pack_id])
            items.append(
                item(
                    "knowledge_pack",
                    pack_id,
                    pack_labels.get(pack_id, pack_id),
                    description="Selección parcial",
                    relation="uses_partial",
                    via=via,
                )
            )
            items.append(
                item(
                    "knowledge",
                    knowledge_id,
                    label,
                    relation="contains",
                    via=("knowledge_pack", pack_id),
                    path=relative_path,
                )
            )
            continue
        items.append(
            item("knowledge", knowledge_id, label, relation="uses", via=via)
        )

    for kind, field in (("skill", "skills"), ("prompt", "prompts"), ("tool", "tools")):
        for resource_id in (str(value) for value in (agent.get(field) or [])):
            items.append(
                item(
                    kind,
                    resource_id,
                    labels[kind].get(resource_id, resource_id),
                    relation="uses",
                    via=via,
                )
            )

    memory_file = str(agent.get("memory_file") or "")
    if agent.get("use_memory") and memory_file:
        # El id de memoria es compuesto ("owner_id::filename") porque el
        # nombre solo es único por dueño.
        memory_key = f"{str(agent.get('owner_id') or '')}::{memory_file}"
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/resource_relations:memory_by_id"),
                (memory_file, str(agent.get("owner_id") or "")),
            )
        if row is not None:
            items.append(
                item("memory", memory_key, memory_file, relation="uses", via=via)
            )
    return items

async def _admin_used_by_agents(
    resource_type: str, resource_id: str, root: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Agentes que usan el recurso raíz.

    Es el único punto que recorre una tabla entera, y no hay forma de
    evitarlo: las referencias de un agente viven dentro de su JSON, así que
    no se pueden filtrar en SQL. Antes se recorrían once.
    """
    import app.config.data as _cfg
    from app.storage.agent_storage import AgentStorage

    agents = await AgentStorage(_cfg.AGENTS_DIR).list(scope="all")
    field = {
        "connection": "connection_id",
        "knowledge": "knowledge",
        "skill": "skills",
        "prompt": "prompts",
        "tool": "tools",
    }.get(resource_type)

    memory_owner_id, _, memory_filename = resource_id.partition("::")
    items: List[Dict[str, Any]] = []
    for agent in agents:
        if resource_type == "memory":
            related = (
                str(agent.get("owner_id") or "") == memory_owner_id
                and bool(agent.get("use_memory"))
                and str(agent.get("memory_file") or "") == memory_filename
            )
        elif resource_type == "llm_orchestration":
            from app.models.llm_orchestration import orchestration_connection_id

            related = str(
                agent.get("connection_id") or ""
            ) == orchestration_connection_id(resource_id)
        elif field == "connection_id":
            operation_connections = {
                str(value).split("::", 1)[0]
                for value in (agent.get("op_connections") or [])
            }
            related = (
                str(agent.get(field) or "") == resource_id
                or resource_id in operation_connections
            )
        elif field:
            related = resource_id in {
                str(value) for value in (agent.get(field) or [])
            }
        else:
            related = False
        if not related:
            continue

        agent_id = str(agent["id"])
        agent_item = item(
            "agent",
            agent_id,
            str(agent.get("name") or agent_id),
            description=str(agent.get("description") or ""),
            relation="uses",
            inverse=True,
        )
        if resource_type == "knowledge":
            pack_id = str(root.get("pack_id") or "")
            if pack_id:
                # El agente usa el fichero, no el pack entero: se enseña la
                # selección parcial para que no parezca que usa todo.
                pack_labels = await admin_labels("knowledge_pack", [pack_id])
                items.append(agent_item)
                items.append(
                    item(
                        "knowledge_pack",
                        pack_id,
                        pack_labels.get(pack_id, pack_id),
                        description="Selección parcial",
                        relation="uses_partial",
                        via=("agent", agent_id),
                    )
                )
                continue
        items.append(agent_item)
    return items

async def _admin_workflows_of_agent(agent_id: str) -> List[Dict[str, Any]]:
    """Orquestaciones que ejecutan un agente (su definición es JSON)."""
    from app.storage.workflows import WorkflowStorage

    items: List[Dict[str, Any]] = []
    for workflow in await WorkflowStorage().list_all():
        agent_ids = {
            str(node.get("agent_id") or "")
            for node in (workflow.get("definition") or {}).get("nodes", [])
        }
        if agent_id in agent_ids:
            workflow_id = str(workflow["id"])
            items.append(
                item(
                    "workflow",
                    workflow_id,
                    str(workflow.get("name") or workflow_id),
                    relation="orchestrates",
                    inverse=True,
                )
            )
    return items
