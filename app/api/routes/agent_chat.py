"""Chat SSE de agentes y contabilización de uso."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, MEMORY_DIR, SKILLS_DIR
from app.config.session import RATE_CHAT_CALLS, RATE_CHAT_WINDOW, RATE_IP_FACTOR
from app.errors import APIError
from app.middleware.locale import get_locale
from app.middleware.ratelimit import RateLimiter, principal_key
from app.models.llm_orchestration import orchestration_id_from_connection
from app.models.request_bodies import AgentChatBody
from app.services.agent_access import agent_access
from app.services.agent_presentation import apply_agent_locale
from app.services.chat import stream_chat
from app.services.llm_executor import try_acquire_llm_lease
from app.services.llm_routing import stream_orchestrated_chat
from app.storage.agent_storage import AgentStorage
from app.storage.chat import ChatStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.db import PH, open_db
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.utils import flog

router = APIRouter(prefix="/api/agents", tags=["agent-chat"])

_agents = AgentStorage(AGENTS_DIR)
_conns = ConnectionStorage()
_skills = SkillStorage(SKILLS_DIR)
_prompts = PromptStorage()
_tools = ToolStorage()
_memory = MemoryStorage(MEMORY_DIR)
_groups = GroupStorage()
_chat = ChatStorage()
_knowledge = KnowledgeStorage()
_knowledge_packs = KnowledgePackStorage()
# Por usuario y no por IP: quien gasta la llamada al LLM es la cuenta, y detrás
# de un NAT corporativo la IP es la de toda la oficina.
_chat_limiter = RateLimiter(
    calls=RATE_CHAT_CALLS,
    window=RATE_CHAT_WINDOW,
    key_func=principal_key,
    shared=True,
    name="agent-chat",
    ip_calls=RATE_CHAT_CALLS * RATE_IP_FACTOR,
)


async def _assert_can_read_agent(
    agent_id: str, agent: Dict[str, Any], ctx: GroupContext
) -> None:
    await agent_access.assert_can_read(agent_id, agent, ctx)


def _apply_locale(agent: Dict[str, Any], locale: str) -> Dict[str, Any]:
    return apply_agent_locale(agent, locale, AGENTS_DIR)


@router.post("/{agent_id}/chat")
async def chat(
    agent_id: str,
    request: Request,
    body: AgentChatBody,
    ctx: GroupContext = Depends(require_group_session),
    _rl: None = Depends(_chat_limiter),
) -> StreamingResponse:
    user, group_id = ctx.user, ctx.group_id
    a = await _agents.get(agent_id)
    if not a:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    if not a.get("is_active", True):
        raise APIError(
            409,
            "resource_inactive",
            "Este agente está desactivado",
            extra={"resource": "agent"},
        )
    role = await get_user_role(user)
    await _assert_can_read_agent(agent_id, a, ctx)
    a = _apply_locale(a, get_locale())

    body = body.payload()
    history: List[Dict[str, Any]] = body.get("messages") or []
    conversation_id: str = str(body.get("conversation_id") or "").strip()

    # Knowledge adjuntado puntualmente desde el chat (selección "@" del
    # usuario): se resuelve y autoriza aquí (no dentro de stream_chat) porque
    # requiere consultar permisos de grupo, ajenos al servicio de chat.
    attached_knowledge: List[Dict[str, Any]] = []
    requested_ids = [
        str(kid) for kid in (body.get("attached_knowledge_ids") or []) if kid
    ][:5]
    for kid in requested_ids:
        item = await _knowledge.get(kid, owner_id=user)
        if not item and group_id != user:
            if await _groups.has_resource_permission(
                group_id, user, "knowledge", kid, "view"
            ):
                item = await _knowledge.get(kid, owner_id=None)
        if item:
            attached_knowledge.append(item)

    # Toda selección es un connection_id. Las orquestaciones se resuelven como
    # conexiones virtuales para que el agente no conozca tipos de destino.
    raw_conn_id = a.get("connection_id") or ""

    # Preferencia por usuario/agente: también debe aplicarse al propietario.
    # La extensión usa esto para cambiar de modelo sin modificar el agente ni
    # la conexión predeterminada para los demás usuarios.
    async with open_db() as _pref_conn:
        _pref_row = await _pref_conn.fetchone(
            f"SELECT connection_id FROM user_agent_preferences "
            f"WHERE username={PH} AND agent_id={PH}",
            (user, agent_id),
        )
    if _pref_row and _pref_row["connection_id"]:
        raw_conn_id = _pref_row["connection_id"]

    if "::" in raw_conn_id:
        base_conn_id, ollama_model = raw_conn_id.split("::", 1)
    else:
        base_conn_id, ollama_model = raw_conn_id, None

    if (
        group_id != user
        and role != "admin"
        and base_conn_id
        and not orchestration_id_from_connection(base_conn_id)
        and not await _groups.has_resource_permission(
            group_id, user, "connections", base_conn_id, "via_agent"
        )
    ):
        raise APIError(
            403,
            "forbidden",
            "No tienes permiso para usar esta conexión mediante agentes",
        )

    if group_id != user and role != "admin":
        for operation_connection_id in a.get("op_connections") or []:
            operation_connection_id = str(operation_connection_id).split("::", 1)[0]
            if operation_connection_id and not await _groups.has_resource_permission(
                group_id,
                user,
                "connections",
                operation_connection_id,
                "via_agent",
            ):
                raise APIError(
                    403,
                    "forbidden",
                    (
                        "No tienes permiso para usar una de las conexiones "
                        "operativas del agente"
                    ),
                )

    conn = None
    if base_conn_id:
        if role == "admin" and not orchestration_id_from_connection(base_conn_id):
            conn = await _conns.get(base_conn_id, None)
        else:
            from app.services.connection_access import connection_access

            conn = await connection_access.get_accessible(base_conn_id, user, group_id)
    memory_store = _memory
    knowledge_store = _knowledge

    if conn and ollama_model:
        conn = {**conn, "model": ollama_model}

    if not conn:
        raise APIError(
            422, "agent_no_connection", "El agente no tiene conexión configurada"
        )

    orchestration = conn.get("_llm_orchestration")
    orchestration_connections = conn.get("_connections") or {}
    if orchestration and group_id != user and role != "admin":
        for target_id in orchestration_connections:
            if not await _groups.has_resource_permission(
                group_id, user, "connections", target_id, "via_agent"
            ):
                raise APIError(
                    403,
                    "forbidden",
                    "No tienes permiso para usar una conexión de la orquestación",
                )

    # Reservar antes de crear StreamingResponse permite responder con un 429
    # real. Si se esperase a iterar el generador, las cabeceras SSE ya serían 200.
    llm_lease = try_acquire_llm_lease()
    if llm_lease is None:
        raise APIError(
            429,
            "llm_capacity_exceeded",
            (
                "El servidor está atendiendo el máximo de conversaciones "
                "simultáneas. Inténtalo de nuevo en unos segundos."
            ),
            headers={"Retry-After": "5"},
        )

    from starlette.background import BackgroundTask

    done_event: List[dict] = []

    history_user_id = user

    async def _gen():
        try:
            streamer = (
                stream_chat(
                    a,
                    conn,
                    history,
                    _skills,
                    memory_store,
                    knowledge_store,
                    _chat,
                    history_user_id,
                    conversation_id or None,
                    knowledge_pack_storage=_knowledge_packs,
                    prompt_storage=_prompts,
                    tool_storage=_tools,
                    attached_knowledge=attached_knowledge,
                    llm_lease=llm_lease,
                )
                if orchestration is None
                else stream_orchestrated_chat(
                    a,
                    orchestration,
                    orchestration_connections,
                    history,
                    _skills,
                    memory_store,
                    knowledge_store,
                    _chat,
                    history_user_id,
                    conversation_id or None,
                    knowledge_pack_storage=_knowledge_packs,
                    prompt_storage=_prompts,
                    tool_storage=_tools,
                    attached_knowledge=attached_knowledge,
                    llm_lease=llm_lease,
                )
            )
            async for chunk in streamer:
                yield chunk
                if chunk.startswith("data: "):
                    try:
                        ev = json.loads(chunk[6:].strip())
                        if ev.get("type") == "done" or (
                            ev.get("type") == "error" and ev.get("usage_by_connection")
                        ):
                            done_event.append(ev)
                    except (json.JSONDecodeError, AttributeError) as exc:
                        flog.warning(
                            f"[agents] Evento SSE inválido para {agent_id}: {exc}"
                        )
        finally:
            llm_lease.release_if_unused()

    async def _on_done():
        if not done_event:
            return
        ev = done_event[0]
        usage_by_connection = ev.get("usage_by_connection") or {}
        if usage_by_connection:
            tok_in = sum(
                int(value.get("in") or 0) for value in usage_by_connection.values()
            )
            tok_out = sum(
                int(value.get("out") or 0) for value in usage_by_connection.values()
            )
        else:
            tokens = ev.get("tokens") or {}
            tok_in = int(tokens.get("in") or 0)
            tok_out = int(tokens.get("out") or 0)
        if usage_by_connection:
            for usage_connection_id, usage in usage_by_connection.items():
                usage_in = int(usage.get("in") or 0)
                usage_out = int(usage.get("out") or 0)
                if usage_in or usage_out:
                    await _conns.add_tokens(usage_connection_id, usage_in, usage_out)
        elif base_conn_id and (tok_in or tok_out):
            await _conns.add_tokens(base_conn_id, tok_in, tok_out)
        if (tok_in or tok_out) and a.get("scope", "private") == "private":
            await _agents.add_tokens(
                agent_id, tok_in, tok_out, owner_id=a.get("owner_id")
            )
            if ev.get("type") != "done":
                return
            if conversation_id:
                reply = ev.get("reply", "")
                user_msg = next(
                    (m for m in reversed(history) if m.get("role") == "user"), None
                )
                if user_msg:
                    await _chat.add_message(
                        conversation_id, "user", str(user_msg.get("content") or "")
                    )
                if reply:
                    await _chat.add_message(
                        conversation_id,
                        "assistant",
                        reply,
                        tokens_in=tok_in,
                        tokens_out=tok_out,
                    )
                    title = str(user_msg.get("content") or "")[:80] if user_msg else ""
                    await _chat.touch_conversation(conversation_id, title)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(_on_done),
    )
