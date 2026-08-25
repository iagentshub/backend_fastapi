"""Concurrent execution engine for directed visual workflows with bounded loops."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Set

from app.models.agent import Agent
from app.services.chat import stream_chat
from app.services.llm_routing import stream_orchestrated_chat
from app.services.resource_stores import (
    _knowledge_packs_store,
    _knowledge_store,
    _prompts_store,
    _skills_store,
    _tools_store,
)
from app.services.workflow_errors import WorkflowPublicError
from app.storage.connection_storage import ConnectionStorage

AgentResolver = Callable[[str], Awaitable[tuple[Dict[str, Any], Dict[str, Any]]]]
QuotaConsumer = Callable[[int], Awaitable[None]]
WORKFLOW_HEARTBEAT_SECONDS = 10.0
_connections = ConnectionStorage()


def _sequence_edges(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        edge
        for edge in definition["edges"]
        if str(edge.get("type") or "sequence") == "sequence"
    ]


def execution_order(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a stable topological order while intentionally ignoring return edges."""
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


def _graph(
    definition: Dict[str, Any],
) -> tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    node_ids = [node["id"] for node in definition["nodes"]]
    predecessors = {node_id: [] for node_id in node_ids}
    successors = {node_id: [] for node_id in node_ids}
    for edge in _sequence_edges(definition):
        predecessors[edge["target"]].append(edge["source"])
        successors[edge["source"]].append(edge["target"])
    return predecessors, successors


def _reachable(start: str, adjacency: Dict[str, List[str]]) -> Set[str]:
    pending = [start]
    seen: Set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(adjacency[current])
    return seen


def _loop_scope(
    edge: Dict[str, Any],
    predecessors: Dict[str, List[str]],
    successors: Dict[str, List[str]],
) -> Set[str]:
    return _reachable(edge["target"], successors) & _reachable(
        edge["source"], predecessors
    )


def _node_input(
    node_id: str,
    initial_input: str,
    outputs: Dict[str, str],
    predecessors: Dict[str, List[str]],
    labels: Dict[str, str],
) -> str:
    sources = predecessors[node_id]
    if not sources:
        return initial_input
    if len(sources) == 1:
        return outputs[sources[0]]
    sections = [f"### {labels[source]}\n{outputs[source]}" for source in sources]
    return (
        "Resultados paralelos que debes consolidar para este paso:\n\n"
        + "\n\n".join(sections)
    )


async def _agent_reply(
    agent: Agent,
    connection: Dict[str, Any],
    content: str,
) -> str:
    for attempt in range(2):
        reply = ""
        try:
            history = [{"role": "user", "content": content}]
            if connection.get("_llm_orchestration"):
                streamer = stream_orchestrated_chat(
                    agent,
                    connection["_llm_orchestration"],
                    connection.get("_connections") or {},
                    history,
                    _skills_store,
                    knowledge_storage=_knowledge_store,
                    knowledge_pack_storage=_knowledge_packs_store,
                    prompt_storage=_prompts_store,
                    tool_storage=_tools_store,
                    resolved_tools=agent.resolved_tools,
                )
            else:
                streamer = stream_chat(
                    agent,
                    connection,
                    history,
                    _skills_store,
                    knowledge_storage=_knowledge_store,
                    knowledge_pack_storage=_knowledge_packs_store,
                    prompt_storage=_prompts_store,
                    tool_storage=_tools_store,
                    resolved_tools=agent.resolved_tools,
                )
            async for chunk in streamer:
                if not chunk.startswith("data: "):
                    continue
                try:
                    event = json.loads(chunk[6:].strip())
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "error":
                    for connection_id, usage in (
                        event.get("usage_by_connection") or {}
                    ).items():
                        await _connections.add_tokens(
                            str(connection_id),
                            int(usage.get("in") or 0),
                            int(usage.get("out") or 0),
                        )
                    raise WorkflowPublicError(
                        str(event.get("code") or "upstream_error"),
                        str(event.get("message") or "Error del agente"),
                    )
                if event.get("type") == "done":
                    reply = str(event.get("reply") or "")
                    usage_by_connection = event.get("usage_by_connection") or {}
                    if usage_by_connection:
                        for connection_id, usage in usage_by_connection.items():
                            await _connections.add_tokens(
                                str(connection_id),
                                int(usage.get("in") or 0),
                                int(usage.get("out") or 0),
                            )
                    else:
                        tokens = event.get("tokens") or {}
                        connection_id = str(connection.get("id") or "")
                        if connection_id:
                            await _connections.add_tokens(
                                connection_id,
                                int(tokens.get("in") or 0),
                                int(tokens.get("out") or 0),
                            )
        except asyncio.CancelledError:
            raise
        except WorkflowPublicError:
            raise
        except Exception as exc:
            raise WorkflowPublicError(
                "upstream_error", f"El agente {agent.name} no pudo responder"
            ) from exc
        if reply.strip():
            return reply
        if attempt == 0:
            content = (
                f"{content}\n\nLa respuesta anterior llegó vacía. "
                "Responde ahora con el contenido solicitado y sus marcadores."
            )
    raise WorkflowPublicError(
        "upstream_error",
        f"El agente {agent.name} no devolvió respuesta tras reintentarlo",
    )


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
    raise WorkflowPublicError(
        "upstream_error",
        f"El evaluador {agent.name} no devolvió una decisión válida",
    )


async def run_workflow(
    definition: Dict[str, Any],
    initial_input: str,
    resolve: AgentResolver,
    *,
    consume_quota: QuotaConsumer | None = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    ordered_nodes = execution_order(definition)
    if len(ordered_nodes) != len(definition["nodes"]):
        raise WorkflowPublicError(
            "invalid_field", "El flujo principal no es un grafo acíclico válido"
        )

    predecessors, successors = _graph(definition)
    loops_by_source = {
        edge["source"]: edge
        for edge in definition["edges"]
        if edge.get("type") == "loop"
    }
    loop_scopes = {
        source: _loop_scope(edge, predecessors, successors)
        for source, edge in loops_by_source.items()
    }
    resolved: Dict[str, tuple[Dict[str, Any], Agent, Dict[str, Any]]] = {}
    for node in ordered_nodes:
        raw_agent, connection = await resolve(node["agent_id"])
        resolved[node["id"]] = (node, Agent.from_dict(raw_agent), connection)

    completed: Set[str] = set()
    outputs: Dict[str, str] = {}
    loop_passes: Dict[str, int] = {}
    execution_count = 0
    labels = {
        node["id"]: str(node.get("label") or resolved[node["id"]][1].name)
        for node in ordered_nodes
    }

    def iteration_for(node_id: str) -> int:
        return max(
            [
                loop_passes.get(source, 1)
                for source, scope in loop_scopes.items()
                if node_id in scope
            ]
            or [1]
        )

    def reset_scope(source_id: str) -> None:
        for scoped_id in loop_scopes[source_id]:
            completed.discard(scoped_id)
            outputs.pop(scoped_id, None)

    while len(completed) < len(ordered_nodes):
        ready = [
            node
            for node in ordered_nodes
            if node["id"] not in completed
            and all(source in completed for source in predecessors[node["id"]])
        ]
        if not ready:
            raise WorkflowPublicError(
                "invalid_field",
                "La orquestación no puede continuar por sus dependencias",
            )

        # Se cobra trabajo ejecutado, no el mero arranque. Una tanda paralela
        # consume tantas unidades como nodos va a enviar realmente al LLM.
        if consume_quota is not None:
            await consume_quota(len(ready))

        tasks: List[asyncio.Task[Any]] = []
        task_meta: List[tuple[Dict[str, Any], Agent, str, int, int | None]] = []
        for node in ready:
            _, agent, connection = resolved[node["id"]]
            iteration = iteration_for(node["id"])
            content = _node_input(
                node["id"], initial_input, outputs, predecessors, labels
            )
            event_index: int | None = None
            if node.get("kind") == "evaluator":
                yield {
                    "type": "evaluation_started",
                    "node_id": node["id"],
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "iteration": iteration,
                }
                task = asyncio.create_task(
                    _evaluate(
                        agent,
                        connection,
                        node["evaluator"]["condition"],
                        content,
                        iteration,
                    )
                )
            else:
                execution_count += 1
                event_index = execution_count
                yield {
                    "type": "stage_started",
                    "index": event_index,
                    "total": len(ordered_nodes),
                    "node_id": node["id"],
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "iteration": iteration,
                }
                instruction = str(node.get("instruction") or "").strip()
                user_content = content
                if instruction:
                    source_label = (
                        "Entrada inicial"
                        if not predecessors[node["id"]]
                        else "Resultados de pasos anteriores"
                    )
                    user_content = (
                        f"Objetivo específico de este paso:\n{instruction}\n\n"
                        f"{source_label}:\n{content}"
                    )
                task = asyncio.create_task(
                    _agent_reply(agent, connection, user_content)
                )
            tasks.append(task)
            task_meta.append((node, agent, content, iteration, event_index))

        gathered = asyncio.gather(*tasks, return_exceptions=True)
        try:
            while not gathered.done():
                done, _ = await asyncio.wait(
                    {gathered}, timeout=WORKFLOW_HEARTBEAT_SECONDS
                )
                if not done:
                    # Mantener viva la respuesta SSE aunque todos los agentes de
                    # esta tanda sigan pensando. Sin este evento, un proxy
                    # intermedio puede cerrar el stream por inactividad.
                    yield {"type": "heartbeat"}
            results = await gathered
        finally:
            if not gathered.done():
                gathered.cancel()
                with suppress(asyncio.CancelledError):
                    await gathered
        error = next(
            (result for result in results if isinstance(result, BaseException)),
            None,
        )
        if error is not None:
            raise error

        restarted = False
        for (node, agent, content, iteration, event_index), result in zip(
            task_meta, results, strict=True
        ):
            node_id = node["id"]
            loop = loops_by_source.get(node_id)
            if node.get("kind") == "evaluator":
                decision = result
                yield {
                    "type": "evaluation_done",
                    "node_id": node_id,
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "iteration": iteration,
                    **decision,
                }
                if decision["approved"]:
                    outputs[node_id] = content
                    completed.add(node_id)
                    continue
                if iteration >= node["evaluator"]["max_iterations"]:
                    yield {
                        "type": "loop_limit_reached",
                        "node_id": node_id,
                        "iteration": iteration,
                        "message": (
                            f"El evaluador {agent.name} no aprobó el resultado "
                            f"en {iteration} vueltas"
                        ),
                    }
                    raise WorkflowPublicError(
                        "invalid_field",
                        f"El ciclo alcanzó {iteration} vueltas sin cumplir la condición",
                    )
                loop_passes[node_id] = iteration + 1
                reset_scope(node_id)
                yield {
                    "type": "loop_iteration_started",
                    "node_id": node_id,
                    "target_node_id": loop["target"],
                    "iteration": iteration + 1,
                }
                restarted = True
                continue

            reply = str(result)
            outputs[node_id] = reply
            completed.add(node_id)
            yield {
                "type": "stage_done",
                "index": event_index,
                "total": len(ordered_nodes),
                "node_id": node_id,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "output": reply,
                "iteration": iteration,
            }
            if loop and loop["mode"] == "fixed" and iteration < loop["iterations"]:
                loop_passes[node_id] = iteration + 1
                reset_scope(node_id)
                yield {
                    "type": "loop_iteration_started",
                    "node_id": node_id,
                    "target_node_id": loop["target"],
                    "iteration": iteration + 1,
                }
                restarted = True

        if restarted:
            continue

    final_nodes = [node_id for node_id, targets in successors.items() if not targets]
    yield {"type": "workflow_done", "output": outputs[final_nodes[0]]}
