"""Sequential execution engine with bounded visual workflow loops."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List

from app.models.agent import Agent
from app.services.chat import stream_chat

AgentResolver = Callable[[str], Awaitable[tuple[Dict[str, Any], Dict[str, Any]]]]


def _sequence_edges(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        edge
        for edge in definition["edges"]
        if str(edge.get("type") or "sequence") == "sequence"
    ]


def execution_order(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the stable main path while intentionally ignoring return edges."""
    nodes = {node["id"]: node for node in definition["nodes"]}
    incoming = {node_id: 0 for node_id in nodes}
    outgoing = {node_id: [] for node_id in nodes}
    for edge in _sequence_edges(definition):
        incoming[edge["target"]] += 1
        outgoing[edge["source"]].append(edge["target"])
    queue = [node_id for node_id, count in incoming.items() if count == 0]
    ordered: List[Dict[str, Any]] = []
    while queue:
        node_id = queue.pop(0)
        ordered.append(nodes[node_id])
        for target in outgoing[node_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return ordered


async def _agent_reply(
    agent: Agent,
    connection: Dict[str, Any],
    content: str,
) -> str:
    reply = ""
    async for chunk in stream_chat(
        agent,
        connection,
        [{"role": "user", "content": content}],
        None,
    ):
        if not chunk.startswith("data: "):
            continue
        try:
            event = json.loads(chunk[6:].strip())
        except json.JSONDecodeError:
            continue
        if event.get("type") == "error":
            raise RuntimeError(str(event.get("message") or "Error del agente"))
        if event.get("type") == "done":
            reply = str(event.get("reply") or "")
    if not reply:
        raise RuntimeError(f"El agente {agent.name} no devolvió respuesta")
    return reply


def _evaluation_payload(reply: str) -> Dict[str, Any] | None:
    try:
        value = json.loads(reply.strip())
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("approved"), bool)
        or not isinstance(value.get("reason"), str)
    ):
        return None
    return {
        "approved": value["approved"],
        "reason": value["reason"].strip()[:1_000],
    }


async def _evaluate(
    agent: Agent,
    connection: Dict[str, Any],
    condition: str,
    output: str,
    iteration: int,
) -> Dict[str, Any]:
    prompt = (
        "Actúas como evaluador de control de una orquestación. "
        "Decide si el resultado cumple la condición indicada.\n\n"
        f"Condición:\n{condition}\n\n"
        f"Resultado de la vuelta {iteration}:\n{output}\n\n"
        'Responde únicamente JSON válido: {"approved": true|false, '
        '"reason": "explicación breve"}.'
    )
    for attempt in range(2):
        reply = await _agent_reply(
            agent,
            connection,
            prompt
            if attempt == 0
            else (
                f"{prompt}\n\nLa respuesta anterior no era JSON válido. "
                "No añadas markdown ni texto fuera del objeto JSON."
            ),
        )
        parsed = _evaluation_payload(reply)
        if parsed is not None:
            return parsed
    raise RuntimeError(f"El evaluador {agent.name} no devolvió una decisión válida")


async def run_workflow(
    definition: Dict[str, Any],
    initial_input: str,
    resolve: AgentResolver,
) -> AsyncGenerator[Dict[str, Any], None]:
    ordered_nodes = execution_order(definition)
    index_by_id = {node["id"]: index for index, node in enumerate(ordered_nodes)}
    loops_by_source = {
        edge["source"]: edge
        for edge in definition["edges"]
        if edge.get("type") == "loop"
    }
    resolved: Dict[str, tuple[Dict[str, Any], Agent, Dict[str, Any]]] = {}
    for node in ordered_nodes:
        raw_agent, connection = await resolve(node["agent_id"])
        resolved[node["id"]] = (node, Agent.from_dict(raw_agent), connection)

    current = initial_input
    loop_passes: Dict[str, int] = {}
    execution_count = 0
    pointer = 0
    while pointer < len(ordered_nodes):
        node = ordered_nodes[pointer]
        _, agent, connection = resolved[node["id"]]
        loop = loops_by_source.get(node["id"])
        iteration = loop_passes.get(node["id"], 1)

        if node.get("kind") == "evaluator":
            evaluator = node["evaluator"]
            yield {
                "type": "evaluation_started",
                "node_id": node["id"],
                "agent_id": agent.id,
                "agent_name": agent.name,
                "iteration": iteration,
            }
            decision = await _evaluate(
                agent,
                connection,
                evaluator["condition"],
                current,
                iteration,
            )
            yield {
                "type": "evaluation_done",
                "node_id": node["id"],
                "agent_id": agent.id,
                "agent_name": agent.name,
                "iteration": iteration,
                **decision,
            }
            if decision["approved"]:
                pointer += 1
                continue
            if iteration >= evaluator["max_iterations"]:
                yield {
                    "type": "loop_limit_reached",
                    "node_id": node["id"],
                    "iteration": iteration,
                    "message": (
                        f"El evaluador {agent.name} no aprobó el resultado "
                        f"en {iteration} vueltas"
                    ),
                }
                raise RuntimeError(
                    f"El ciclo alcanzó {iteration} vueltas sin cumplir la condición"
                )
            loop_passes[node["id"]] = iteration + 1
            yield {
                "type": "loop_iteration_started",
                "node_id": node["id"],
                "target_node_id": loop["target"],
                "iteration": iteration + 1,
            }
            pointer = index_by_id[loop["target"]]
            continue

        execution_count += 1
        yield {
            "type": "stage_started",
            "index": execution_count,
            "total": len(ordered_nodes),
            "node_id": node["id"],
            "agent_id": agent.id,
            "agent_name": agent.name,
            "iteration": iteration,
        }
        instruction = str(node.get("instruction") or "").strip()
        user_content = current
        if instruction:
            source_label = "Entrada inicial" if execution_count == 1 else "Resultado anterior"
            user_content = (
                f"Objetivo específico de este paso:\n{instruction}\n\n"
                f"{source_label}:\n{current}"
            )
        reply = await _agent_reply(agent, connection, user_content)
        current = reply
        yield {
            "type": "stage_done",
            "index": execution_count,
            "total": len(ordered_nodes),
            "node_id": node["id"],
            "agent_id": agent.id,
            "agent_name": agent.name,
            "output": reply,
            "iteration": iteration,
        }

        if loop and loop["mode"] == "fixed":
            required = loop["iterations"]
            if iteration < required:
                loop_passes[node["id"]] = iteration + 1
                yield {
                    "type": "loop_iteration_started",
                    "node_id": node["id"],
                    "target_node_id": loop["target"],
                    "iteration": iteration + 1,
                }
                pointer = index_by_id[loop["target"]]
                continue
        pointer += 1

    yield {"type": "workflow_done", "output": current}
