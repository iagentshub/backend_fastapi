"""Sequential execution engine for validated agent workflows."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List

from app.models.agent import Agent
from app.services.chat import stream_chat

AgentResolver = Callable[[str], Awaitable[tuple[Dict[str, Any], Dict[str, Any]]]]


def execution_order(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = {node["id"]: node for node in definition["nodes"]}
    incoming = {node_id: 0 for node_id in nodes}
    outgoing = {node_id: [] for node_id in nodes}
    for edge in definition["edges"]:
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


async def run_workflow(
    definition: Dict[str, Any],
    initial_input: str,
    resolve: AgentResolver,
) -> AsyncGenerator[Dict[str, Any], None]:
    ordered_nodes = execution_order(definition)
    resolved = []
    for node in ordered_nodes:
        raw_agent, connection = await resolve(node["agent_id"])
        resolved.append((node, Agent.from_dict(raw_agent), connection))

    current = initial_input
    total = len(resolved)
    for index, (node, agent, connection) in enumerate(resolved, start=1):
        yield {
            "type": "stage_started",
            "index": index,
            "total": total,
            "node_id": node["id"],
            "agent_id": agent.id,
            "agent_name": agent.name,
        }
        instruction = str(node.get("instruction") or "").strip()
        user_content = current
        if instruction:
            source_label = "Entrada inicial" if index == 1 else "Resultado anterior"
            user_content = (
                f"Objetivo específico de este paso:\n{instruction}\n\n"
                f"{source_label}:\n{current}"
            )
        reply = ""
        async for chunk in stream_chat(
            agent,
            connection,
            [{"role": "user", "content": user_content}],
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
        current = reply
        yield {
            "type": "stage_done",
            "index": index,
            "total": total,
            "node_id": node["id"],
            "agent_id": agent.id,
            "agent_name": agent.name,
            "output": reply,
        }
    yield {"type": "workflow_done", "output": current}
