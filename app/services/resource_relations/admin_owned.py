"""De quién es un recurso y qué hay en su espacio, visto desde Admin."""


from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.services.resource_relations._shared import (
    _pack_member_items,
    admin_labels,
    item,
)
from app.services.resource_relations.admin_uses import _admin_agent_uses
from app.sql import sql
from app.storage.db import open_db


async def _admin_origin_items(
    resource_type: str,
    resource_id: str,
    resource: Dict[str, Any],
    owner: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """De dónde salió el recurso: fuente oficial, proveedor o su propietario.

    Devuelve la cadena entera (propietario → origen → recurso) para que el
    grafo enseñe la procedencia y no solo la posesión.
    """
    if owner is None:
        return []

    lookup_id = resource_id
    if resource_type == "memory" and "::" in resource_id:
        _, _, filename = resource_id.partition("::")
        lookup_id = filename.removesuffix(".md")

    async with open_db() as conn:
        link = await conn.fetchone(
            sql("queries/resource_relations:source_link_of_resource"),
            (resource_type, resource_id, owner["id"]),
        )
        if link is None and lookup_id != resource_id:
            link = await conn.fetchone(
                sql("queries/resource_relations:source_link_of_resource"),
                (resource_type, lookup_id, owner["id"]),
            )
        if link is not None:
            source = await conn.fetchone(
                sql("queries/resource_relations:source_by_id"),
                (str(link["source_id"]),),
            )
            if source is not None:
                return [
                    item(
                        "official_source",
                        str(source["id"]),
                        str(source["name"] or source["id"]),
                        description=str(source["repository_url"] or ""),
                        relation="owns",
                        inverse=True,
                    ),
                    item(
                        owner["type"],
                        owner["id"],
                        owner["label"],
                        relation="owns",
                        via=("official_source", str(source["id"])),
                        inverse=True,
                    ),
                ]

        if resource_type == "connection":
            account_id = str(resource.get("provider_account_id") or "")
            if account_id:
                account = await conn.fetchone(
                    sql("queries/resource_relations:account_by_id"),
                    (account_id, owner["id"]),
                )
                if account is not None:
                    try:
                        data = json.loads(account["data"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        data = {}
                    provider = str(account["provider"] or "")
                    return [
                        item(
                            "provider",
                            account_id,
                            str(data.get("name") or provider or account_id),
                            description=provider,
                            relation="provides",
                            inverse=True,
                        ),
                        item(
                            owner["type"],
                            owner["id"],
                            owner["label"],
                            relation="owns",
                            via=("provider", account_id),
                            inverse=True,
                        ),
                    ]

    return [
        item(
            owner["type"],
            owner["id"],
            owner["label"],
            relation="owns",
            inverse=True,
        )
    ]

async def _admin_group_items(
    resource_type: str, resource_id: str, root: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Pertenencia a grupos y recursos compartidos con un grupo."""
    items: List[Dict[str, Any]] = []
    async with open_db() as conn:
        if resource_type == "user":
            rows = await conn.fetchall(
                sql("queries/resource_relations:group_memberships_of_user"),
                (str(root.get("username") or resource_id),),
            )
            group_ids = [str(row["group_id"]) for row in rows]
            labels = await admin_labels("group", group_ids)
            for row in rows:
                group_id = str(row["group_id"])
                if group_id not in labels:
                    continue
                items.append(
                    item(
                        "group",
                        group_id,
                        labels[group_id],
                        relation=f"member:{row['role']}",
                    )
                )
        elif resource_type == "group":
            rows = await conn.fetchall(
                sql("queries/resource_relations:members_of_group"), (resource_id,)
            )
            for row in rows:
                member = await conn.fetchone(
                    sql("queries/resource_relations:user_by_username"),
                    (str(row["username"]),),
                )
                if member is None:
                    continue
                items.append(
                    item(
                        "user",
                        str(member["id"]),
                        str(member["username"]),
                        relation=f"member:{row['role']}",
                        inverse=True,
                    )
                )
            shares = await conn.fetchall(
                sql("queries/resource_relations:shares_of_group"), (resource_id,)
            )
            for share in shares:
                kind = str(share["resource_type"])
                shared_id = str(share["resource_id"])
                labels = await admin_labels(kind, [shared_id])
                if shared_id not in labels:
                    continue
                items.append(
                    item(kind, shared_id, labels[shared_id], relation="shared")
                )
                if kind == "knowledge_pack":
                    items.extend(
                        await _pack_member_items(
                            shared_id, via=("knowledge_pack", shared_id)
                        )
                    )
        else:
            shares = await conn.fetchall(
                sql("queries/resource_relations:shares_of_resource"),
                (resource_type, resource_id),
            )
            group_ids = [str(share["group_id"]) for share in shares]
            labels = await admin_labels("group", group_ids)
            for group_id in group_ids:
                if group_id not in labels:
                    continue
                items.append(
                    item(
                        "group",
                        group_id,
                        labels[group_id],
                        relation="shared",
                        inverse=True,
                    )
                )
    return items

async def _admin_owned_items(owner_id: str) -> List[Dict[str, Any]]:
    """Todo lo que posee un usuario o grupo, consultado por `owner_id`."""
    import app.config.data as _cfg
    from app.storage.agent_storage import AgentStorage
    from app.storage.connection_storage import ConnectionStorage
    from app.storage.knowledge import KnowledgeStorage
    from app.storage.knowledge_packs import KnowledgePackStorage
    from app.storage.llm_orchestrations import LLMOrchestrationStorage
    from app.storage.memory_storage import MemoryStorage
    from app.storage.prompt_storage import PromptStorage
    from app.storage.skill_storage import SkillStorage
    from app.storage.tool_storage import ToolStorage
    from app.storage.workflows import WorkflowStorage

    # Dos consultas por dueño resuelven la procedencia de todos sus recursos:
    # de qué repositorio oficial vino cada uno y de qué cuenta de proveedor
    # cuelga cada conexión.
    async with open_db() as conn:
        link_rows = await conn.fetchall(
            sql("queries/resource_relations:source_links_of_owner"), (owner_id,)
        )
        account_rows = await conn.fetchall(
            sql("queries/resource_relations:accounts_of_owner"), (owner_id,)
        )
    origins = {
        (str(row["resource_type"]), str(row["resource_id"])): row for row in link_rows
    }
    accounts = {str(row["id"]): row for row in account_rows}
    sources_emitidas: set[str] = set()
    proveedores_emitidos: set[str] = set()

    def _origen(kind: str, resource_id: str) -> tuple[Optional[tuple[str, str]], List[Dict[str, Any]]]:
        """De quién cuelga el recurso y qué nodos hay que emitir antes."""
        row = origins.get((kind, resource_id))
        if row is None and kind == "memory" and "::" in resource_id:
            _, _, filename = resource_id.partition("::")
            row = origins.get((kind, filename.removesuffix(".md")))
        if row is None:
            return None, []
        source_id = str(row["source_id"])
        previos: List[Dict[str, Any]] = []
        if source_id not in sources_emitidas:
            sources_emitidas.add(source_id)
            previos.append(
                item(
                    "official_source",
                    source_id,
                    str(row["name"] or source_id),
                    description=str(row["repository_url"] or ""),
                    relation="owns",
                )
            )
        return ("official_source", source_id), previos

    items: List[Dict[str, Any]] = []
    agents = await AgentStorage(_cfg.AGENTS_DIR).list(scope="private", owner_id=owner_id)
    for agent in agents:
        agent_id = str(agent["id"])
        via, previos = _origen("agent", agent_id)
        items.extend(previos)
        items.append(
            item(
                "agent",
                agent_id,
                str(agent.get("name") or agent_id),
                description=str(agent.get("description") or ""),
                relation="origin" if via else "owns",
                via=via,
            )
        )
        items.extend(await _admin_agent_uses(agent, via=("agent", agent_id)))

    simples: List[tuple[str, List[Dict[str, Any]]]] = [
        (
            "skill",
            await SkillStorage(_cfg.SKILLS_DIR).list(
                scope="private", owner_id=owner_id
            ),
        ),
        ("prompt", await PromptStorage().list(scope="private", owner_id=owner_id)),
        ("tool", await ToolStorage().list(scope="private", owner_id=owner_id)),
        ("connection", await ConnectionStorage().list(owner_id=owner_id)),
        ("knowledge_pack", await KnowledgePackStorage().list(owner_id)),
        ("workflow", await WorkflowStorage().list(owner_id)),
        ("llm_orchestration", await LLMOrchestrationStorage().list(owner_id)),
    ]
    for kind, rows in simples:
        for row in rows:
            row_id = str(row["id"])
            via, previos = _origen(kind, row_id)
            if via is None and kind == "connection":
                account_id = str(row.get("provider_account_id") or "")
                account = accounts.get(account_id)
                if account is not None:
                    try:
                        data = json.loads(account["data"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        data = {}
                    provider = str(account["provider"] or "")
                    if account_id not in proveedores_emitidos:
                        proveedores_emitidos.add(account_id)
                        previos.append(
                            item(
                                "provider",
                                account_id,
                                str(data.get("name") or provider or account_id),
                                description=provider,
                                relation="owns",
                            )
                        )
                    via = ("provider", account_id)
            items.extend(previos)
            items.append(
                item(
                    kind,
                    row_id,
                    str(row.get("name") or row_id),
                    description=str(row.get("description") or ""),
                    relation="provides"
                    if via and via[0] == "provider"
                    else "origin"
                    if via
                    else "owns",
                    via=via,
                )
            )
            if kind == "knowledge_pack":
                items.extend(
                    await _pack_member_items(row_id, via=("knowledge_pack", row_id))
                )

    for knowledge in await KnowledgeStorage().list(owner_id):
        # Los ficheros de un pack ya salen colgando de su pack.
        if knowledge.get("pack_id"):
            continue
        knowledge_id = str(knowledge["id"])
        items.append(
            item(
                "knowledge",
                knowledge_id,
                str(knowledge.get("title") or knowledge.get("name") or knowledge_id),
                relation="owns",
            )
        )

    for memory in await MemoryStorage(_cfg.MEMORY_DIR).list(owner_id):
        filename = str(memory.get("id") or "")
        memory_id = f"{owner_id}::{filename}"
        via, previos = _origen("memory", memory_id)
        items.extend(previos)
        items.append(
            item(
                "memory",
                memory_id,
                filename,
                relation="origin" if via else "owns",
                via=via,
            )
        )
    return items
