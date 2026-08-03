"""AI-assisted agent builder routes."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import GroupContext, require_group
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.config.providers import PROVIDER_DEFAULT_MODELS
from app.errors import APIError
from app.models.agent import Agent
from app.services.agent_builder import (
    BuilderMessage,
    BuilderMode,
    BuilderResources,
    build_fallback_ready,
    build_system_prompt,
    can_build_without_model,
    parse_builder_reply,
    should_force_ready,
)
from app.services.chat import stream_chat
from app.storage.groups import GroupStorage
from app.storage.guest import get_session, is_guest
from app.storage.storage import ConnectionStorage

router = APIRouter(prefix="/api/agent-builder", tags=["agent-builder"])
logger = logging.getLogger(__name__)
_conns = ConnectionStorage(DB_FILE)
_groups = GroupStorage(DB_FILE)


class BuilderChatBody(BaseModel):
    connection_id: str = Field(min_length=1, max_length=300)
    messages: List[BuilderMessage] = Field(min_length=1, max_length=30)
    resources: BuilderResources = Field(default_factory=BuilderResources)
    mode: BuilderMode = "auto"


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def builder_chat(
    body: BuilderChatBody,
    ctx: GroupContext = Depends(require_group),
) -> StreamingResponse:
    user, group_id = ctx.user, ctx.group_id
    raw_conn_id = body.connection_id
    if "::" in raw_conn_id:
        conn_id, ollama_model = raw_conn_id.split("::", 1)
    else:
        conn_id, ollama_model = raw_conn_id, None

    if is_guest(user):
        conn = next(
            (item for item in get_session(user).connections if item.get("id") == conn_id),
            None,
        )
    else:
        role = await get_user_role(user)
        if (
            group_id != user
            and role != "admin"
            and not await _groups.has_resource_permission(
                group_id, user, "connections", conn_id, "direct"
            )
        ):
            raise APIError(
                403, "forbidden", "No tienes permiso para usar esta conexión directamente"
            )
    if not is_guest(user) and role == "admin":
        conn = await _conns.get(conn_id, None)
    elif not is_guest(user):
        from app.api.routes.connections import _get_conn_any

        conn = await _get_conn_any(conn_id, user, group_id)
    if conn and ollama_model:
        conn = {**conn, "model": ollama_model}
    if not conn:
        raise APIError(
            404,
            "not_found",
            "La conexión seleccionada no existe o no está disponible",
            extra={"resource": "connection"},
        )

    # Una especificación extensa ya contiene suficiente contexto. Para entradas
    # breves permitimos una aclaración; en el segundo turno se genera siempre.
    force_ready = should_force_ready(body.messages, body.mode)
    if can_build_without_model(body.messages, body.mode):
        envelope = build_fallback_ready(body.messages, body.resources, body.mode)

        async def generate_complete_specification():
            yield _sse(
                {
                    "type": "builder_done",
                    **envelope.model_dump(mode="json"),
                }
            )

        return StreamingResponse(
            generate_complete_specification(),
            media_type="text/event-stream",
        )

    builder_conn = conn
    if str(conn.get("type") or "").lower() == "nvidia":
        builder_conn = {
            **conn,
            "model": PROVIDER_DEFAULT_MODELS["nvidia"],
        }

    builder_agent = Agent(
        id="_agent_builder",
        name="Constructor de agentes",
        model=str(builder_conn.get("model") or ""),
        system_prompt=build_system_prompt(
            body.resources,
            force_ready=force_ready,
            mode=body.mode,
        ),
        temperature=0.2,
        # Da margen para un prompt profesional con proceso, controles y límites,
        # sin permitir que un modelo pequeño divague indefinidamente.
        max_tokens=2200,
        # La conexión del asistente debe usar un modelo rápido. Tres minutos
        # cubren colas/cold starts de NIM sin dejar la interfaz bloqueada.
        timeout=90,
    )
    history = [message.model_dump() for message in body.messages]

    async def generate():
        attempt_history = history
        last_issue = "El proveedor no devolvió respuesta"
        for attempt in range(2):
            reply = ""
            partial_reply = ""
            provider_error = ""
            async for chunk in stream_chat(
                builder_agent, builder_conn, attempt_history, None
            ):
                if chunk.startswith(":"):
                    yield chunk
                    continue
                if not chunk.startswith("data: "):
                    continue
                try:
                    event = json.loads(chunk[6:].strip())
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "token":
                    partial_reply += str(event.get("token") or "")
                    yield _sse({"type": "progress"})
                elif event.get("type") == "error":
                    provider_error = str(event.get("message") or "")
                    break
                elif event.get("type") == "done":
                    reply = str(event.get("reply") or "")

            candidate = reply or partial_reply
            if candidate:
                try:
                    envelope = parse_builder_reply(candidate, body.resources)
                except ValueError as exc:
                    last_issue = str(exc)
                    logger.warning(
                        "Agent builder returned invalid structured output "
                        "(%s chars): %s",
                        len(candidate),
                        exc,
                    )
                else:
                    if not force_ready or envelope.status == "ready":
                        yield _sse(
                            {
                                "type": "builder_done",
                                **envelope.model_dump(mode="json"),
                            }
                        )
                        return
                    last_issue = (
                        "El modelo hizo otra pregunta cuando ya debía crear el borrador"
                    )

            if provider_error:
                last_issue = provider_error
                is_timeout = (
                    "timed out" in provider_error.lower()
                    or "timeout" in provider_error.lower()
                )
                if attempt == 0 and is_timeout:
                    yield _sse({"type": "progress"})
                    continue
                if force_ready:
                    fallback = build_fallback_ready(
                        body.messages, body.resources, body.mode
                    )
                    yield _sse(
                        {
                            "type": "builder_done",
                            **fallback.model_dump(mode="json"),
                        }
                    )
                    return
                message = (
                    "El modelo seleccionado tardó demasiado en responder incluso "
                    "después de reintentarlo. Elige un modelo rápido para el "
                    "asistente, como Llama 3B u 8B."
                    if is_timeout
                    else provider_error
                )
                yield _sse({"type": "error", "message": message})
                return

            if attempt == 0:
                attempt_history = [
                    *history,
                    *(
                        [{"role": "assistant", "content": reply}]
                        if reply
                        else []
                    ),
                    {
                        "role": "user",
                        "content": (
                            "Corrige tu respuesta anterior. No hagas más preguntas. "
                            "Diseña el agente solicitado y devuelve únicamente el "
                            "objeto JSON completo con status=\"ready\" y draft."
                        ),
                    },
                ]
                yield _sse({"type": "progress"})

        if force_ready:
            fallback = build_fallback_ready(
                body.messages, body.resources, body.mode
            )
            yield _sse(
                {
                    "type": "builder_done",
                    **fallback.model_dump(mode="json"),
                }
            )
            return
        yield _sse(
            {
                "type": "error",
                "message": (
                    "El modelo no pudo producir un borrador válido. "
                    f"Detalle: {last_issue}"
                ),
            }
        )

    return StreamingResponse(generate(), media_type="text/event-stream")
