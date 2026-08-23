"""Relaciones de un recurso publicado en el marketplace.

Filtra por lo que es público: de un agente publicado solo cuelga lo que también
lo está, porque enseñar una dependencia privada en Explorar es enseñar algo que
quien lo enlace no va a poder usar.
"""


from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.resource_relations._shared import (
    _knowledge_pack_of,
    _pack_member_items,
    item,
    payload,
)
from app.sql import sql
from app.storage.db import open_db

_PUBLIC_VAL = 1

# Tipos de recurso que un agente puede referenciar, con el campo del agente
# donde se guardan sus ids. El orden fija el de las relaciones devueltas.
_AGENT_REFERENCES: tuple[tuple[str, str], ...] = (
    ("skill", "skills"),
    ("knowledge_pack", "knowledge_packs"),
    ("knowledge", "knowledge"),
    ("prompt", "prompts"),
    ("tool", "tools"),
)

async def public_names(
    resource_type: str, resource_ids: List[str]
) -> Dict[str, Dict[str, str]]:
    """Nombre y descripción públicos de varios recursos, en una consulta.

    El grafo público hacía un `fetchone` por dependencia; con un agente que
    declara veinte, eran veinte viajes a la base de datos para pintar un
    diálogo.
    """
    unique = [value for value in dict.fromkeys(resource_ids) if value]
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT resource_id, name, description FROM resource_social "
            f"WHERE resource_type=? AND resource_id IN ({placeholders}) "
            "AND is_public=?",
            (resource_type, *unique, _PUBLIC_VAL),
        )
    return {
        str(row["resource_id"]): {
            "name": row["name"] or "",
            "description": row["description"] or "",
        }
        for row in rows
    }


async def _workflow_agent_presentations(
    agent_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Presentación pública/legible de los agentes de un workflow, en lote.

    El id del agente no se devuelve en la relación pública: el paso conserva
    su id sintético y las dependencias solo se añaden si el agente está
    publicado. El nombre almacenado únicamente evita que una copia enlazada o
    un agente compartido termine presentado como el id técnico ``step-*``.
    """
    unique = [value for value in dict.fromkeys(agent_ids) if value]
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    query = sql("queries/resource_relations:workflow_agent_presentations").replace(
        "@agent_ids@", placeholders
    )
    async with open_db() as conn:
        rows = await conn.fetchall(query, tuple(unique))
    return {
        str(row["id"]): {
            "stored_name": str(row["stored_name"] or "").strip(),
            "public_name": str(row["public_name"] or "").strip(),
            "public_description": str(row["public_description"] or ""),
            "is_public": bool(row["is_public"]),
        }
        for row in rows
    }


async def _agent_public_items(
    agent: Dict[str, Any], *, via: Optional[tuple[str, str]]
) -> List[Dict[str, Any]]:
    """Dependencias públicas de un agente, sin revelar las privadas.

    `public_dependencies` es la razón por la que esto vive en el servidor y no
    en el cliente: decide qué dependencias de un recurso publicado se enseñan,
    y un cliente no puede filtrar lo que no debería haber recibido.
    """
    raw_selection = agent.get("public_dependencies")
    selected = (
        {str(value) for value in raw_selection if value}
        if raw_selection is not None
        else None
    )

    wanted: Dict[str, List[str]] = {}
    for kind, field in _AGENT_REFERENCES:
        ids = [str(value) for value in (agent.get(field) or []) if value]
        if selected is not None:
            ids = [value for value in ids if f"{kind}:{value}" in selected]
        if ids:
            wanted[kind] = ids

    names = {kind: await public_names(kind, ids) for kind, ids in wanted.items()}
    items: List[Dict[str, Any]] = []
    knowledge_pack_ids: set[str] = set()

    for kind, _ in _AGENT_REFERENCES:
        for resource_id in wanted.get(kind, []):
            info = names.get(kind, {}).get(resource_id)
            if info is None:
                continue
            if kind == "knowledge":
                # Un fichero suelto de un pack se enseña bajo su pack, para
                # que se vea que la selección es parcial.
                pack_id, relative_path = await _knowledge_pack_of(resource_id)
                if pack_id:
                    if pack_id not in knowledge_pack_ids:
                        knowledge_pack_ids.add(pack_id)
                        pack_names = await public_names("knowledge_pack", [pack_id])
                        items.append(
                            item(
                                "knowledge_pack",
                                pack_id,
                                pack_names.get(pack_id, {}).get("name", pack_id),
                                relation="uses_partial",
                                via=via,
                            )
                        )
                    items.append(
                        item(
                            "knowledge",
                            resource_id,
                            info["name"],
                            description=info["description"],
                            relation="contains",
                            via=("knowledge_pack", pack_id),
                            path=relative_path,
                        )
                    )
                    continue
            items.append(
                item(
                    kind,
                    resource_id,
                    info["name"],
                    description=info["description"],
                    relation="uses",
                    via=via,
                )
            )
            if kind == "knowledge_pack":
                knowledge_pack_ids.add(resource_id)
                items.extend(
                    await _pack_member_items(resource_id, via=("knowledge_pack", resource_id))
                )

    memory_file = str(agent.get("memory_file") or "").strip()
    if (
        agent.get("use_memory")
        and memory_file
        and (selected is None or f"memory:{memory_file}" in selected)
    ):
        items.append(
            item(
                "memory",
                memory_file,
                memory_file,
                description="Memoria publicada con el agente",
                relation="uses",
                via=via,
            )
        )
    return items

async def public_relations(
    resource_type: str, resource_id: str
) -> Optional[Dict[str, Any]]:
    """Relaciones públicas de un recurso publicado en Explorar.

    Devuelve None si el recurso no existe o no es público.
    """
    import app.config.data as _cfg
    from app.storage.agent_storage import AgentStorage
    from app.storage.workflows import WorkflowStorage

    async with open_db() as conn:
        published = await conn.fetchone(
            sql("queries/explore:social_name_desc"),
            (resource_type, resource_id, _PUBLIC_VAL),
        )
    if not published:
        return None

    items: List[Dict[str, Any]] = []
    if resource_type == "knowledge_pack":
        # `via` apunta al propio pack, que es la raíz: los miembros cuelgan
        # de ella sin caso especial.
        items = await _pack_member_items(
            resource_id, via=("knowledge_pack", resource_id)
        )
    elif resource_type == "agent":
        agent = await AgentStorage(_cfg.AGENTS_DIR).get(resource_id)
        if agent:
            items = await _agent_public_items(agent, via=None)
    elif resource_type == "workflow":
        items = await _workflow_public_items(
            resource_id, await WorkflowStorage().get_any(resource_id)
        )
    else:  # pragma: no cover - los tipos válidos los filtra la ruta
        return None

    return payload(
        root_type=resource_type,
        root_id=resource_id,
        root_label=published["name"] or resource_id,
        root_description=published["description"] or "",
        items=items,
    )

async def _workflow_public_items(
    workflow_id: str, workflow: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Pasos de una orquestación pública y lo que usa cada agente público."""
    import app.config.data as _cfg
    from app.storage.agent_storage import AgentStorage

    definition = (workflow or {}).get("definition") or {}
    raw_nodes = definition.get("nodes") or []
    raw_edges = definition.get("edges") or []

    agent_ids = [str(node.get("agent_id") or "") for node in raw_nodes]
    agent_presentations = await _workflow_agent_presentations(agent_ids)

    items: List[Dict[str, Any]] = []
    step_ids: Dict[str, str] = {}
    incoming: set[str] = set()
    storage = AgentStorage(_cfg.AGENTS_DIR)

    for index, raw_node in enumerate(raw_nodes):
        raw_step_id = str(raw_node.get("id") or f"step-{index}")
        step_id = f"{workflow_id}:{raw_step_id}"
        step_ids[raw_step_id] = step_id
        agent_id = str(raw_node.get("agent_id") or "")
        presentation = agent_presentations.get(agent_id) or {}
        is_public_agent = presentation.get("is_public") is True
        kind = "evaluator" if raw_node.get("kind") == "evaluator" else "agent"
        label = (
            presentation.get("public_name")
            or str(raw_node.get("label") or "").strip()
            or presentation.get("stored_name")
            or f"Agente {index + 1}"
        )
        items.append(
            item(
                kind,
                step_id,
                label,
                description=(
                    presentation.get("public_description", "")
                    if is_public_agent
                    else ""
                ),
                relation="orchestrates",
            )
        )
        if is_public_agent:
            agent = await storage.get(agent_id)
            if agent:
                items.extend(await _agent_public_items(agent, via=(kind, step_id)))

    for raw_edge in raw_edges:
        source = step_ids.get(str(raw_edge.get("source") or ""))
        target = step_ids.get(str(raw_edge.get("target") or ""))
        if not source or not target:
            continue
        incoming.add(target)

    # Un paso con predecesor cuelga de él, no de la raíz.
    by_step: Dict[str, Dict[str, Any]] = {}
    for entry in items:
        if entry["relation"] == "orchestrates":
            by_step[entry["id"]] = entry
    for raw_edge in raw_edges:
        source = step_ids.get(str(raw_edge.get("source") or ""))
        target = step_ids.get(str(raw_edge.get("target") or ""))
        if not source or not target or target not in by_step:
            continue
        entry = by_step[target]
        entry["via"] = {"type": by_step[source]["type"], "id": source}
        entry["relation"] = (
            "flow_loop" if raw_edge.get("type") == "loop" else "flow"
        )
    return items

async def official_pack_relations(
    requester_id: str, source_id: str
) -> Optional[Dict[str, Any]]:
    """Componentes de un pack oficial y las dependencias entre ellos."""
    from app.services.official_pack_service import OfficialPackService

    detail = await OfficialPackService().detail(requester_id, source_id)
    if detail is None:
        return None

    items = [
        item(
            component.resource_type,
            component.resource_id,
            component.name,
            description=component.description,
            relation="origin",
        )
        for component in detail.components
    ]
    by_key = {
        component.component_key: component for component in detail.components
    }
    for component in detail.components:
        for dependency in component.dependencies:
            target = by_key.get(dependency)
            if target is None:
                continue
            items.append(
                item(
                    target.resource_type,
                    target.resource_id,
                    target.name,
                    description=target.description,
                    relation="uses",
                    via=(component.resource_type, component.resource_id),
                )
            )
    return payload(
        root_type="official_source",
        root_id=source_id,
        root_label=detail.pack.name,
        root_description=detail.pack.repository_url,
        items=items,
    )
