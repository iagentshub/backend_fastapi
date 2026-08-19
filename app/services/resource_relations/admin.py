"""Relaciones de cualquier recurso, sin filtro de visibilidad — solo Admin.

Es la vista que el propietario no tiene: aquí sí cuelgan los recursos privados
y los de otros usuarios.
"""


from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.resource_relations._shared import (
    _ADMIN_TABLES,
    _pack_member_items,
    admin_labels,
    item,
    payload,
)
from app.services.resource_relations.admin_owned import (
    _admin_group_items,
    _admin_origin_items,
    _admin_owned_items,
)
from app.services.resource_relations.admin_uses import (
    _admin_agent_uses,
    _admin_used_by_agents,
    _admin_workflows_of_agent,
)
from app.sql import sql
from app.storage.db import open_db


async def _admin_owner_item(owner_id: str) -> Optional[Dict[str, Any]]:
    """El usuario o grupo propietario, resuelto por id."""
    if not owner_id:
        return None
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/resource_relations:user_by_id"), (owner_id,)
        )
        if row:
            return {"type": "user", "id": owner_id, "label": str(row["username"])}
        row = await conn.fetchone(
            sql("queries/resource_relations:group_by_id"), (owner_id,)
        )
        if row:
            return {"type": "group", "id": owner_id, "label": str(row["name"])}
    return None

async def _admin_resource(
    resource_type: str, resource_id: str
) -> Optional[Dict[str, Any]]:
    """El recurso raíz, leído de su propia tabla por id.

    Los tipos que solo aportan su nombre se leen con una consulta; los que
    además necesitan sus referencias (que viven en JSON) pasan por su storage,
    que es quien sabe decodificarlas.
    """
    import app.config.data as _cfg
    from app.storage.agent_storage import AgentStorage
    from app.storage.knowledge_packs import KnowledgePackStorage
    from app.storage.llm_orchestrations import LLMOrchestrationStorage
    from app.storage.workflows import WorkflowStorage

    if resource_type == "agent":
        return await AgentStorage(_cfg.AGENTS_DIR).get(resource_id)
    if resource_type == "knowledge_pack":
        return await KnowledgePackStorage().get(resource_id, include_items=False)
    if resource_type == "workflow":
        return await WorkflowStorage().get_any(resource_id)
    if resource_type == "llm_orchestration":
        return await LLMOrchestrationStorage().get_any(resource_id)
    if resource_type == "memory":
        owner_id, _, filename = resource_id.partition("::")
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/resource_relations:memory_by_id"), (filename, owner_id)
            )
        if row is None:
            return None
        return {"id": resource_id, "owner_id": owner_id, "name": filename}
    if resource_type == "user":
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/resource_relations:user_by_id"), (resource_id,)
            )
            if row is None:
                row = await conn.fetchone(
                    sql("queries/resource_relations:user_by_username"), (resource_id,)
                )
        return dict(row) if row else None
    if resource_type == "group":
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/resource_relations:group_by_id"), (resource_id,)
            )
        return dict(row) if row else None

    if resource_type not in _ADMIN_TABLES:
        return None
    table, name_column = _ADMIN_TABLES[resource_type]
    extra = ", provider_account_id" if resource_type == "connection" else ""
    extra += ", pack_id" if resource_type == "knowledge" else ""
    async with open_db() as conn:
        row = await conn.fetchone(
            f"SELECT id, owner_id, {name_column} AS name{extra} FROM {table} "
            "WHERE id = ? LIMIT 1",
            (resource_id,),
        )
    return dict(row) if row else None

async def admin_relations(
    resource_type: str, resource_id: str
) -> Optional[Dict[str, Any]]:
    """Vecindario de un recurso en el panel de administración.

    Todas las consultas van dirigidas al recurso pedido. La versión anterior
    empezaba cargando el inventario completo de la instalación —once listados
    sin filtro, más todos los ficheros de todos los packs, todas las cuentas y
    una consulta por fuente oficial— para acabar dibujando media docena de
    aristas. El coste crecía con el tamaño de la instalación en vez de con el
    del grafo.
    """
    root = await _admin_resource(resource_type, resource_id)
    if root is None:
        return None

    canonical_id = str(root.get("id") or resource_id)
    label_field = {"user": "username", "knowledge": "title"}.get(
        resource_type, "name"
    )
    root_label = str(root.get(label_field) or root.get("name") or canonical_id)
    root_description = str(root.get("description") or root.get("email") or "")

    items: List[Dict[str, Any]] = []
    if resource_type in ("user", "group"):
        items.extend(await _admin_group_items(resource_type, canonical_id, root))
        items.extend(await _admin_owned_items(canonical_id))
    else:
        owner_id = str(root.get("owner_id") or "")
        if resource_type == "memory":
            owner_id = canonical_id.partition("::")[0]
        owner = await _admin_owner_item(owner_id)
        items.extend(
            await _admin_origin_items(resource_type, canonical_id, root, owner)
        )
        items.extend(await _admin_group_items(resource_type, canonical_id, root))

        if resource_type == "agent":
            items.extend(await _admin_agent_uses(root, via=None))
            items.extend(await _admin_workflows_of_agent(canonical_id))
        elif resource_type == "knowledge_pack":
            items.extend(
                await _pack_member_items(
                    canonical_id, via=("knowledge_pack", canonical_id)
                )
            )
        elif resource_type == "workflow":
            agent_ids = [
                str(node.get("agent_id") or "")
                for node in (root.get("definition") or {}).get("nodes", [])
            ]
            labels = await admin_labels("agent", agent_ids)
            for agent_id in dict.fromkeys(value for value in agent_ids if value):
                items.append(
                    item(
                        "agent",
                        agent_id,
                        labels.get(agent_id, agent_id),
                        relation="orchestrates",
                    )
                )
        elif resource_type == "llm_orchestration":
            connection_ids = {
                str(candidate.get("connection_id") or "")
                for candidate in root.get("candidates") or []
            }
            connection_ids.add(str(root.get("router_connection_id") or ""))
            connection_ids.discard("")
            labels = await admin_labels("connection", sorted(connection_ids))
            for connection_id in sorted(connection_ids):
                if connection_id in labels:
                    items.append(
                        item(
                            "connection",
                            connection_id,
                            labels[connection_id],
                            relation="routes",
                        )
                    )
            items.extend(
                await _admin_used_by_agents(resource_type, canonical_id, root)
            )
        else:
            items.extend(
                await _admin_used_by_agents(resource_type, canonical_id, root)
            )

    return payload(
        root_type=resource_type,
        root_id=canonical_id,
        root_label=root_label,
        root_description=root_description,
        items=items,
    )
