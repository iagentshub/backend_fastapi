"""Relaciones de un recurso: el único sitio que las resuelve.

Antes cada pantalla que necesitaba un grafo lo montaba por su cuenta —cuatro
constructores en el cliente y cuatro en el backend, con la misma frase «un
agente usa una skill, un prompt, una tool…» escrita cuatro veces y ya
divergida—. Aquí el servidor aporta solo **hechos**: qué cuelga de qué, con
qué nombre y con qué relación. La forma (qué es raíz, qué nodos de carpeta
hacen falta, qué arista se dibuja punteada) la decide el cliente.

`to_graph` traduce esos hechos al formato `nodes`/`edges` que los endpoints
`/graph` sirvieron hasta ahora, para que durante la convivencia no haya dos
verdades: una sola construcción, dos serializaciones.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

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


def item(
    resource_type: str,
    resource_id: str,
    label: str,
    *,
    relation: str,
    description: str = "",
    via: Optional[tuple[str, str]] = None,
    path: str = "",
    inverse: bool = False,
) -> Dict[str, Any]:
    """Un hecho: qué recurso cuelga de qué otro y con qué relación.

    `via` es el recurso del que cuelga, como (tipo, id); `None` significa que
    cuelga de la raíz. Va como par y no como id suelto porque el mismo id
    puede existir en dos tipos distintos.

    `inverse` invierte la dirección de la arista: un propietario, una fuente
    oficial o el agente que usa el recurso apuntan *hacia* aquello de lo que
    cuelgan, no al revés.
    """
    payload: Dict[str, Any] = {
        "type": resource_type,
        "id": resource_id,
        "label": label or resource_id,
        "description": description,
        "relation": relation,
        "via": {"type": via[0], "id": via[1]} if via else None,
        "inverse": inverse,
    }
    if path:
        payload["path"] = path
    return payload


def payload(
    *,
    root_type: str,
    root_id: str,
    root_label: str,
    root_description: str = "",
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "root": {
            "type": root_type,
            "id": root_id,
            "label": root_label or root_id,
            "description": root_description,
        },
        "items": items,
    }


def node_id(resource_type: str, resource_id: str) -> str:
    return f"{resource_type}:{resource_id}"


def to_graph(relations: Dict[str, Any]) -> Dict[str, Any]:
    """Serializa las relaciones como el grafo que servían los `/graph`.

    Incluye los nodos de carpeta que el cliente construye a partir de `path`:
    el formato viejo los llevaba dentro y hay clientes desplegados que lo
    esperan así.
    """
    root = relations["root"]
    root_node_id = node_id(root["type"], root["id"])
    nodes: List[Dict[str, Any]] = [
        {
            "id": root_node_id,
            "label": root["label"],
            "type": root["type"],
            "description": root.get("description", ""),
        }
    ]
    edges: List[Dict[str, Any]] = []
    seen = {root_node_id}
    directories: Dict[str, str] = {}

    for relation_item in relations["items"]:
        target_id = node_id(relation_item["type"], relation_item["id"])
        via = relation_item.get("via")
        parent_id = node_id(via["type"], via["id"]) if via else root_node_id

        path = relation_item.get("path") or ""
        parts = [part for part in path.split("/") if part]
        if len(parts) > 1:
            # Las carpetas no son recursos: son nodos sintéticos derivados de
            # la ruta relativa del fichero dentro de su pack.
            accumulated: List[str] = []
            # El id conserva el formato que servía el endpoint anterior:
            # knowledge_directory:{id del pack}:{ruta}.
            owner_raw_id = via["id"] if via else root["id"]
            for directory in parts[:-1]:
                accumulated.append(directory)
                key = f"{owner_raw_id}:{'/'.join(accumulated)}"
                directory_id = directories.get(key)
                if directory_id is None:
                    directory_id = f"knowledge_directory:{key}"
                    directories[key] = directory_id
                    nodes.append(
                        {
                            "id": directory_id,
                            "label": directory,
                            "type": "knowledge_directory",
                            "description": "/".join(accumulated),
                        }
                    )
                    edges.append(
                        {
                            "source_id": parent_id,
                            "target_id": directory_id,
                            "relation": "contains",
                        }
                    )
                parent_id = directory_id

        if target_id not in seen:
            seen.add(target_id)
            nodes.append(
                {
                    "id": target_id,
                    "label": relation_item["label"],
                    "type": relation_item["type"],
                    "description": relation_item.get("description", ""),
                }
            )
        source, target = (
            (target_id, parent_id)
            if relation_item.get("inverse")
            else (parent_id, target_id)
        )
        edge: Dict[str, Any] = {
            "source_id": source,
            "target_id": target,
            "relation": relation_item["relation"],
        }
        if relation_item["relation"] in ("flow_loop", "shared", "depends"):
            edge["dashed"] = True
        edges.append(edge)

    return {"root_id": root_node_id, "nodes": nodes, "edges": edges}


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


async def _knowledge_pack_of(knowledge_id: str) -> tuple[str, str]:
    """Pack y ruta relativa de un fichero de knowledge, o ("", "")."""
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/resource_relations:knowledge_pack_of"), (knowledge_id,)
        )
    if not row:
        return "", ""
    return str(row["pack_id"] or ""), str(row["pack_relative_path"] or "")


async def _pack_member_items(
    pack_id: str, *, via: tuple[str, str]
) -> List[Dict[str, Any]]:
    """Ficheros de un pack con su ruta: el árbol lo arma el cliente."""
    async with open_db() as conn:
        rows = await conn.fetchall(
            sql("queries/resource_relations:pack_members"), (pack_id,)
        )
    return [
        item(
            "knowledge",
            str(row["id"]),
            str(row["pack_relative_path"] or row["name"] or row["id"]),
            description=str(row["pack_kind"] or ""),
            relation="contains",
            via=via,
            path=str(row["pack_relative_path"] or ""),
        )
        for row in rows
    ]


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
    public_agents = await public_names("agent", agent_ids)

    items: List[Dict[str, Any]] = []
    step_ids: Dict[str, str] = {}
    incoming: set[str] = set()
    storage = AgentStorage(_cfg.AGENTS_DIR)

    for index, raw_node in enumerate(raw_nodes):
        raw_step_id = str(raw_node.get("id") or f"step-{index}")
        step_id = f"{workflow_id}:{raw_step_id}"
        step_ids[raw_step_id] = step_id
        agent_id = str(raw_node.get("agent_id") or "")
        public_agent = public_agents.get(agent_id)
        kind = "evaluator" if raw_node.get("kind") == "evaluator" else "agent"
        items.append(
            item(
                kind,
                step_id,
                (public_agent or {}).get("name")
                or str(raw_node.get("label") or raw_step_id),
                description=(public_agent or {}).get("description", ""),
                relation="orchestrates",
            )
        )
        if public_agent:
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


# Tabla y columna de nombre de cada tipo de recurso del panel de
# administración. La tabla nunca viene del usuario: la ruta valida el tipo
# contra este mapa antes de llegar aquí.
_ADMIN_TABLES: Dict[str, tuple[str, str]] = {
    "agent": ("agents", "name"),
    "skill": ("skills", "name"),
    "prompt": ("prompts", "name"),
    "tool": ("tools", "name"),
    "connection": ("connections", "name"),
    "knowledge": ("knowledge_items", "title"),
    "knowledge_pack": ("knowledge_packs", "name"),
    "workflow": ("agent_workflows", "name"),
    "llm_orchestration": ("llm_orchestrations", "name"),
    "user": ("users", "username"),
    "group": ("groups", "name"),
}


async def admin_labels(resource_type: str, resource_ids: List[str]) -> Dict[str, str]:
    """Nombre de varios recursos del mismo tipo, en una sola consulta.

    Sin filtro de visibilidad: es la vista de administración. Lo que sí tiene
    es filtro por id — el endpoint anterior resolvía estos nombres cargando el
    inventario completo de la instalación.
    """
    unique = [value for value in dict.fromkeys(resource_ids) if value]
    if not unique or resource_type not in _ADMIN_TABLES:
        return {}
    table, name_column = _ADMIN_TABLES[resource_type]
    placeholders = ",".join("?" for _ in unique)
    async with open_db() as conn:
        rows = await conn.fetchall(
            f"SELECT id, {name_column} AS label FROM {table} "
            f"WHERE id IN ({placeholders})",
            tuple(unique),
        )
    return {str(row["id"]): str(row["label"] or row["id"]) for row in rows}


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
