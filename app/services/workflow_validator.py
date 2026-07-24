"""Validation rules for directed multi-agent workflows."""

from __future__ import annotations

from typing import Any, Dict, List, Set


MAX_NODES = 30
MAX_NODE_ID_LENGTH = 120
MAX_AGENT_ID_LENGTH = 200
MAX_LABEL_LENGTH = 120
MAX_INSTRUCTION_LENGTH = 2_000


def validate_workflow(definition: Dict[str, Any]) -> Dict[str, Any]:
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("La orquestación necesita al menos un agente")
    if len(nodes) > MAX_NODES:
        raise ValueError(f"Una orquestación admite como máximo {MAX_NODES} pasos")
    if not isinstance(edges, list):
        raise ValueError("Las conexiones de la orquestación no son válidas")

    ids: Set[str] = set()
    normalized_nodes: List[Dict[str, str]] = []
    for raw in nodes:
        if not isinstance(raw, dict):
            raise ValueError("Nodo no válido")
        node_id = str(raw.get("id") or "").strip()
        agent_id = str(raw.get("agent_id") or "").strip()
        if not node_id or not agent_id or node_id in ids:
            raise ValueError("Cada nodo necesita un id único y un agente")
        if len(node_id) > MAX_NODE_ID_LENGTH:
            raise ValueError("El identificador de un paso es demasiado largo")
        if len(agent_id) > MAX_AGENT_ID_LENGTH:
            raise ValueError("El identificador de un agente es demasiado largo")
        ids.add(node_id)
        normalized_nodes.append(
            {
                "id": node_id,
                "agent_id": agent_id,
                "label": str(raw.get("label") or "").strip()[:MAX_LABEL_LENGTH],
                "instruction": str(raw.get("instruction") or "").strip()[
                    :MAX_INSTRUCTION_LENGTH
                ],
            }
        )

    graph = {node_id: [] for node_id in ids}
    incoming = {node_id: 0 for node_id in ids}
    seen_edges: Set[tuple[str, str]] = set()
    normalized_edges: List[Dict[str, str]] = []
    for raw in edges:
        if not isinstance(raw, dict):
            raise ValueError("Conexión no válida")
        source = str(raw.get("source") or "")
        target = str(raw.get("target") or "")
        if source not in ids or target not in ids or source == target:
            raise ValueError("Las conexiones deben unir nodos distintos existentes")
        edge = (source, target)
        if edge in seen_edges:
            raise ValueError("La orquestación contiene conexiones duplicadas")
        seen_edges.add(edge)
        graph[source].append(target)
        incoming[target] += 1
        normalized_edges.append({"source": source, "target": target})

    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("La orquestación contiene un ciclo")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in graph[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in ids:
        visit(node_id)

    if len(nodes) == 1:
        if normalized_edges:
            raise ValueError("Una orquestación de un solo paso no necesita conexiones")
        return {"nodes": normalized_nodes, "edges": normalized_edges}

    if len(normalized_edges) != len(nodes) - 1:
        raise ValueError("Todos los pasos deben formar una única secuencia")
    if any(len(targets) > 1 for targets in graph.values()):
        raise ValueError("Un paso no puede enviar su salida a varios pasos")
    if any(count > 1 for count in incoming.values()):
        raise ValueError("Un paso no puede recibir la salida de varios pasos")
    starts = [node_id for node_id, count in incoming.items() if count == 0]
    ends = [node_id for node_id, targets in graph.items() if not targets]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError(
            "La orquestación debe tener un único inicio y un único final"
        )

    return {"nodes": normalized_nodes, "edges": normalized_edges}
