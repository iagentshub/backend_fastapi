"""Enrutado y failover entre conexiones LLM sin romper el streaming SSE."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.models.agent import Agent
from app.services.chat import stream_chat
from app.utils import flog


def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event(frame: str) -> Dict[str, Any] | None:
    if not frame.startswith("data: "):
        return None
    try:
        value = json.loads(frame[6:].strip())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _public_connection(connection: Dict[str, Any]) -> Dict[str, str]:
    return {
        "id": str(connection.get("id") or ""),
        "name": str(connection.get("name") or connection.get("id") or "LLM"),
        "model": str(connection.get("model") or ""),
    }


def _router_prompt(
    agent: Agent,
    history: List[Dict[str, Any]],
    candidates: List[Dict[str, str]],
    connections: Dict[str, Dict[str, Any]],
) -> str:
    last_user = next(
        (
            str(message.get("content") or "")
            for message in reversed(history)
            if message.get("role") == "user"
        ),
        "",
    )
    choices = [
        {
            "connection_id": candidate["connection_id"],
            "name": connections.get(candidate["connection_id"], {}).get(
                "name", candidate["connection_id"]
            ),
            "provider": connections.get(candidate["connection_id"], {}).get(
                "type", "unavailable"
            ),
            "model": connections.get(candidate["connection_id"], {}).get("model", ""),
            "routing_hint": candidate.get("routing_hint", ""),
        }
        for candidate in candidates
    ]
    return (
        "Ordena todas las conexiones candidatas desde la más adecuada hasta la menos "
        "adecuada para ejecutar esta tarea. No ejecutes la tarea.\n\n"
        f"Instrucción del agente:\n{agent.system_prompt}\n\n"
        f"Mensaje actual:\n{last_user}\n\n"
        f"Candidatas:\n{json.dumps(choices, ensure_ascii=False)}\n\n"
        "Responde únicamente JSON válido con esta forma exacta: "
        '{"ranking":[{"connection_id":"id","reason":"motivo breve"}]}. '
        "Incluye cada ID exactamente una vez y no inventes IDs."
    )


def _parse_ranking(
    reply: str, configured_ids: List[str]
) -> tuple[List[str], Dict[str, str]]:
    raw = reply.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    value = json.loads(raw)
    ranking = value.get("ranking") if isinstance(value, dict) else None
    if not isinstance(ranking, list):
        raise ValueError("ranking ausente")
    ids: List[str] = []
    reasons: Dict[str, str] = {}
    for item in ranking:
        if not isinstance(item, dict):
            raise ValueError("entrada de ranking inválida")
        connection_id = str(item.get("connection_id") or "")
        if connection_id in ids:
            raise ValueError("ID repetido")
        ids.append(connection_id)
        reasons[connection_id] = str(item.get("reason") or "")[:500]
    if set(ids) != set(configured_ids) or len(ids) != len(configured_ids):
        raise ValueError("el ranking no coincide con las candidatas")
    return ids, reasons


async def _rank(
    agent: Agent,
    orchestration: Dict[str, Any],
    connections: Dict[str, Dict[str, Any]],
    history: List[Dict[str, Any]],
) -> tuple[List[str], Dict[str, str], Dict[str, Dict[str, int]], str | None]:
    configured = [str(item["connection_id"]) for item in orchestration["candidates"]]
    router_id = str(orchestration.get("router_connection_id") or "")
    router = connections.get(router_id)
    if not router or not router.get("is_active", True):
        return [], {}, {}, "La conexión orquestadora no está disponible"
    router_agent = Agent(
        id="llm-router",
        name="LLM Router",
        system_prompt=(
            "Eres un enrutador determinista. Solo clasificas conexiones y siempre "
            "respondes con el JSON solicitado."
        ),
        temperature=0,
    )
    reply = ""
    usage: Dict[str, Dict[str, int]] = {}
    error: str | None = None
    try:
        async for frame in stream_chat(
            router_agent,
            router,
            [
                {
                    "role": "user",
                    "content": _router_prompt(
                        agent, history, orchestration["candidates"], connections
                    ),
                }
            ],
            None,
        ):
            event = _event(frame)
            if not event:
                continue
            if event.get("type") == "done":
                reply = str(event.get("reply") or "")
                tokens = event.get("tokens") or {}
                usage[router_id] = {
                    "in": int(tokens.get("in") or 0),
                    "out": int(tokens.get("out") or 0),
                }
            elif event.get("type") == "error":
                error = str(event.get("message") or "Error del router")
    except Exception as exc:
        error = str(exc)
    if error:
        return [], {}, usage, error
    try:
        ranking, reasons = _parse_ranking(reply, configured)
        return ranking, reasons, usage, None
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return [], {}, usage, f"Respuesta inválida del orquestador: {exc}"


async def stream_orchestrated_chat(
    agent: Dict[str, Any] | Agent,
    orchestration: Dict[str, Any],
    connections: Dict[str, Dict[str, Any]],
    history: List[Dict[str, Any]],
    skill_storage: Any,
    memory_storage: Any = None,
    knowledge_storage: Any = None,
    chat_storage: Any = None,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    *,
    prompt_storage: Any = None,
    tool_storage: Any = None,
    attached_knowledge: Optional[List[Dict[str, Any]]] = None,
) -> AsyncGenerator[str, None]:
    if not isinstance(agent, Agent):
        agent = Agent.from_dict(agent)
    configured_ids = [
        str(candidate.get("connection_id") or "")
        for candidate in orchestration.get("candidates") or []
    ]
    usage_by_connection: Dict[str, Dict[str, int]] = {}
    reasons: Dict[str, str] = {}
    if orchestration.get("mode") == "balanced":
        ranking, reasons, router_usage, router_error = await _rank(
            agent, orchestration, connections, history
        )
        usage_by_connection.update(router_usage)
        if router_error:
            flog.warning(
                f"[llm-routing] Router {orchestration.get('router_connection_id')} "
                f"falló: {router_error}"
            )
            yield _sse(
                {
                    "type": "routing_warning",
                    "message": "La conexión orquestadora ha fallado; se detiene la orquestación.",
                }
            )
            yield _sse(
                {
                    "type": "error",
                    "message": "No se pudo decidir qué conexión LLM debía ejecutar la tarea.",
                    "routing": {
                        "orchestration_id": orchestration.get("id"),
                        "mode": "balanced",
                        "router_connection_id": orchestration.get(
                            "router_connection_id"
                        ),
                    },
                    "usage_by_connection": usage_by_connection,
                }
            )
            return
    else:
        ranking = configured_ids

    attempted: List[str] = []
    previous: Dict[str, Any] | None = None
    failures: List[str] = []
    for connection_id in ranking:
        connection = connections.get(connection_id)
        if not connection or not connection.get("is_active", True):
            failures.append(f"{connection_id}: no disponible")
            unavailable = {
                "id": connection_id,
                "name": str((connection or {}).get("name") or connection_id),
                "model": str((connection or {}).get("model") or ""),
            }
            yield _sse(
                {
                    "type": "routing_warning",
                    "message": (
                        f"{unavailable['name']} no está disponible; se probará "
                        "la siguiente mejor opción."
                    ),
                }
            )
            previous = unavailable
            continue
        public = _public_connection(connection)
        if previous is None:
            yield _sse(
                {
                    "type": "routing_selected",
                    "connection": public,
                    "reason": reasons.get(connection_id, ""),
                    "message": f"Se usará {public['name']}.",
                }
            )
        else:
            yield _sse(
                {
                    "type": "routing_failover",
                    "failed_connection": previous,
                    "connection": public,
                    "reason": reasons.get(connection_id, ""),
                    "message": (
                        f"{previous['name']} no está disponible; se usará "
                        f"{public['name']}, la siguiente mejor opción."
                    ),
                }
            )
        attempted.append(connection_id)
        emitted = False
        terminal_error: str | None = None
        completed = False
        try:
            async for frame in stream_chat(
                agent,
                connection,
                history,
                skill_storage,
                memory_storage,
                knowledge_storage,
                chat_storage,
                user_id,
                conversation_id,
                prompt_storage=prompt_storage,
                tool_storage=tool_storage,
                attached_knowledge=attached_knowledge,
            ):
                event = _event(frame)
                if event and event.get("type") == "token":
                    emitted = True
                    yield frame
                elif event and event.get("type") == "error":
                    terminal_error = str(event.get("message") or "Error del proveedor")
                    if emitted:
                        yield frame
                        return
                elif event and event.get("type") == "done":
                    reply = str(event.get("reply") or "")
                    if not reply.strip() and not emitted:
                        terminal_error = "El proveedor devolvió una respuesta vacía"
                        continue
                    tokens = event.get("tokens") or {}
                    usage_by_connection[connection_id] = {
                        "in": int(tokens.get("in") or 0),
                        "out": int(tokens.get("out") or 0),
                    }
                    event["routing"] = {
                        "orchestration_id": orchestration.get("id"),
                        "mode": orchestration.get("mode"),
                        "selected_connection_id": connection_id,
                        "attempted_connection_ids": attempted,
                    }
                    event["usage_by_connection"] = usage_by_connection
                    yield _sse(event)
                    completed = True
                elif not event:
                    yield frame
        except Exception as exc:
            terminal_error = str(exc) or type(exc).__name__
            if emitted:
                yield _sse({"type": "error", "message": terminal_error})
                return
        if completed:
            return
        failures.append(f"{public['name']}: {terminal_error or 'fallo desconocido'}")
        flog.warning(f"[llm-routing] Fallo antes del primer token: {failures[-1]}")
        previous = public

    yield _sse(
        {
            "type": "error",
            "message": "No hay ninguna conexión LLM disponible en la orquestación.",
            "attempted_connection_ids": attempted,
        }
    )
