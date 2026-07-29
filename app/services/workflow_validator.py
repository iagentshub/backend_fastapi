"""Validation rules for directed multi-agent workflows."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Set

MAX_NODES = 30
MAX_NODE_ID_LENGTH = 120
MAX_AGENT_ID_LENGTH = 200
MAX_LABEL_LENGTH = 120
MAX_INSTRUCTION_LENGTH = 2_000
MAX_CONDITION_LENGTH = 2_000
MAX_POSITION = 100_000
MIN_LOOP_ITERATIONS = 2
MAX_LOOP_ITERATIONS = 20


def _position(raw: Any) -> Dict[str, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("La posición de un nodo no es válida")
    x = raw.get("x")
    y = raw.get("y")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
        or not math.isfinite(float(x))
        or not math.isfinite(float(y))
        or abs(float(x)) > MAX_POSITION
        or abs(float(y)) > MAX_POSITION
    ):
        raise ValueError("La posición de un nodo no es válida")
    return {"x": round(float(x), 2), "y": round(float(y), 2)}


def _iteration_count(raw: Any, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{label} debe ser un número entero")
    if raw < MIN_LOOP_ITERATIONS or raw > MAX_LOOP_ITERATIONS:
        raise ValueError(
            f"{label} debe estar entre {MIN_LOOP_ITERATIONS} y "
            f"{MAX_LOOP_ITERATIONS}"
        )
    return raw


def _sequence_order(
    node_ids: Set[str], sequence_edges: List[Dict[str, str]]
) -> List[str]:
    if len(node_ids) == 1:
        if sequence_edges:
            raise ValueError("Una orquestación de un solo paso no necesita conexiones")
        return list(node_ids)
    if len(sequence_edges) < len(node_ids) - 1:
        raise ValueError("Todos los pasos deben estar conectados")

    outgoing = {node_id: [] for node_id in node_ids}
    incoming = {node_id: 0 for node_id in node_ids}
    for edge in sequence_edges:
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]] += 1
    starts = [node_id for node_id, count in incoming.items() if count == 0]
    ends = [node_id for node_id, targets in outgoing.items() if not targets]
    if not starts or not ends:
        raise ValueError("La secuencia principal contiene un ciclo")
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("La orquestación debe tener un único inicio y un único final")

    pending = dict(incoming)
    queue = list(starts)
    ordered: List[str] = []
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        for target in outgoing[current]:
            pending[target] -= 1
            if pending[target] == 0:
                queue.append(target)
    if len(ordered) != len(node_ids):
        raise ValueError("El flujo principal contiene un ciclo o pasos desconectados")
    return ordered


def _has_path(
    source: str,
    target: str,
    sequence_edges: List[Dict[str, Any]],
) -> bool:
    outgoing: Dict[str, List[str]] = {}
    for edge in sequence_edges:
        outgoing.setdefault(edge["source"], []).append(edge["target"])
    pending = [source]
    seen: Set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(outgoing.get(current, []))
    return False


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
    normalized_nodes: List[Dict[str, Any]] = []
    nodes_by_id: Dict[str, Dict[str, Any]] = {}
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

        kind = str(raw.get("kind") or "agent")
        if kind not in {"agent", "evaluator"}:
            raise ValueError("El tipo de nodo no es válido")
        node: Dict[str, Any] = {
            "id": node_id,
            "agent_id": agent_id,
            "label": str(raw.get("label") or "").strip()[:MAX_LABEL_LENGTH],
            "instruction": str(raw.get("instruction") or "").strip()[
                :MAX_INSTRUCTION_LENGTH
            ],
            "kind": kind,
        }
        position = _position(raw.get("position"))
        if position is not None:
            node["position"] = position
        if kind == "evaluator":
            evaluator = raw.get("evaluator")
            if not isinstance(evaluator, dict):
                raise ValueError("El evaluador necesita una condición y un límite")
            condition = str(evaluator.get("condition") or "").strip()
            if not condition:
                raise ValueError("El evaluador necesita una condición")
            node["evaluator"] = {
                "condition": condition[:MAX_CONDITION_LENGTH],
                "max_iterations": _iteration_count(
                    evaluator.get("max_iterations", 5),
                    "El máximo de vueltas",
                ),
            }
        ids.add(node_id)
        normalized_nodes.append(node)
        nodes_by_id[node_id] = node

    seen_edges: Set[tuple[str, str, str]] = set()
    sequence_edges: List[Dict[str, Any]] = []
    loop_edges: List[Dict[str, Any]] = []
    for raw in edges:
        if not isinstance(raw, dict):
            raise ValueError("Conexión no válida")
        source = str(raw.get("source") or "")
        target = str(raw.get("target") or "")
        edge_type = str(raw.get("type") or "sequence")
        if source not in ids or target not in ids or source == target:
            raise ValueError("Las conexiones deben unir nodos distintos existentes")
        if edge_type not in {"sequence", "loop"}:
            raise ValueError("El tipo de conexión no es válido")
        edge_key = (source, target, edge_type)
        if edge_key in seen_edges:
            raise ValueError("La orquestación contiene conexiones duplicadas")
        seen_edges.add(edge_key)

        edge: Dict[str, Any] = {
            "source": source,
            "target": target,
            "type": edge_type,
        }
        if edge_type == "sequence":
            sequence_edges.append(edge)
            continue

        mode = str(raw.get("mode") or "fixed")
        if mode not in {"fixed", "condition"}:
            raise ValueError("El modo del ciclo no es válido")
        edge["mode"] = mode
        if mode == "fixed":
            edge["iterations"] = _iteration_count(
                raw.get("iterations", 2), "Las vueltas del ciclo"
            )
        loop_edges.append(edge)

    order = _sequence_order(ids, sequence_edges)
    indexes = {node_id: index for index, node_id in enumerate(order)}
    intervals: List[tuple[int, int]] = []
    loop_sources: Set[str] = set()
    for edge in loop_edges:
        start = indexes[edge["target"]]
        end = indexes[edge["source"]]
        if start >= end or not _has_path(
            edge["target"], edge["source"], sequence_edges
        ):
            raise ValueError("Un ciclo debe volver a un paso anterior")
        if edge["source"] in loop_sources:
            raise ValueError("Un paso no puede cerrar varios ciclos")
        loop_sources.add(edge["source"])
        interval = (start, end)
        if any(
            not (end < other_start or start > other_end)
            for other_start, other_end in intervals
        ):
            raise ValueError("Los ciclos no pueden solaparse ni anidarse")
        intervals.append(interval)

        source_node = nodes_by_id[edge["source"]]
        if edge["mode"] == "condition":
            if source_node["kind"] != "evaluator":
                raise ValueError("Un ciclo por condición necesita un agente evaluador")
        elif source_node["kind"] == "evaluator":
            raise ValueError("Un agente evaluador solo puede cerrar un ciclo por condición")

    evaluator_ids = {
        node["id"] for node in normalized_nodes if node["kind"] == "evaluator"
    }
    condition_sources = {
        edge["source"] for edge in loop_edges if edge["mode"] == "condition"
    }
    if evaluator_ids != condition_sources:
        raise ValueError("Cada evaluador debe cerrar exactamente un ciclo por condición")

    return {"nodes": normalized_nodes, "edges": sequence_edges + loop_edges}
