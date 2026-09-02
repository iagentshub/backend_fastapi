"""AI-assisted agent builder routes."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import GroupContext, require_group_session
from app.api.routes.llm_limits import interactive_llm_limiter
from app.auth.auth import get_user_role
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
from app.services.builder_progress import partial_progress
from app.services.chat import stream_chat
from app.storage.connection_storage import ConnectionStorage
from app.storage.groups import GroupStorage

router = APIRouter(prefix="/api/agent-builder", tags=["agent-builder"])
logger = logging.getLogger(__name__)
_conns = ConnectionStorage()
_groups = GroupStorage()


class BuilderChatBody(BaseModel):
    connection_id: str = Field(min_length=1, max_length=300)
    messages: List[BuilderMessage] = Field(min_length=1, max_length=30)
    resources: BuilderResources = Field(default_factory=BuilderResources)
    mode: BuilderMode = "auto"


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# Los códigos que emite stream_chat y que un segundo intento puede salvar.
# `internal_error` es su red de seguridad final: ahí caen los TimeoutError de
# socket, que no llegan como URLError.
_TRANSIENT_ERROR_CODES = frozenset(
    {"llm_capacity_exceeded", "provider_unreachable", "internal_error"}
)
_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})
_ESPERA_ANTES_DE_REINTENTAR = 1.5


def _is_transient_provider_error(code: str, message: str) -> bool:
    """Return whether retrying a provider failure can reasonably succeed.

    La versión anterior buscaba subcadenas ("timeout", "overloaded", "capacity")
    en el texto del error, y ninguno de los mensajes que stream_chat produce las
    contiene: la capacidad agotada y el proveedor inalcanzable —los dos fallos
    transitorios más frecuentes— se trataban como definitivos. El código sí
    viaja en el evento; el texto solo hace falta para el estado HTTP.
    """
    if code in _TRANSIENT_ERROR_CODES:
        return True
    if code and code != "provider_http_error":
        return False
    status = re.search(r"HTTP (\d{3})", message)
    return status is not None and int(status.group(1)) in _TRANSIENT_HTTP_STATUSES


@router.post("/chat")
async def builder_chat(
    body: BuilderChatBody,
    ctx: GroupContext = Depends(require_group_session),
    _rl: None = Depends(interactive_llm_limiter),
) -> StreamingResponse:
    user, group_id = ctx.user, ctx.group_id
    raw_conn_id = body.connection_id
    if "::" in raw_conn_id:
        conn_id, ollama_model = raw_conn_id.split("::", 1)
    else:
        conn_id, ollama_model = raw_conn_id, None

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
    if role == "admin":
        conn = await _conns.get(conn_id, None)
    else:
        from app.services.connection_access import connection_access

        conn = await connection_access.get_accessible(conn_id, user, group_id)
    if conn and ollama_model:
        conn = {**conn, "model": ollama_model}
    if not conn:
        raise APIError(
            404,
            "not_found",
            "La conexión seleccionada no existe o no está disponible",
            extra={"resource": "connection"},
        )
    if not conn.get("is_active", True):
        raise APIError(
            409,
            "resource_inactive",
            "La conexión seleccionada está desactivada",
            extra={"resource": "connection", "resource_id": conn_id},
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

    builder_agent = Agent(
        id="_agent_builder",
        name="Constructor de agentes",
        model=str(conn.get("model") or ""),
        system_prompt=build_system_prompt(
            body.resources,
            force_ready=force_ready,
            mode=body.mode,
        ),
        temperature=0.2,
        # El envoltorio JSON pide identidad, alcance, método paso a paso,
        # criterios, comprobaciones, límites y contrato de salida, y los saltos
        # de línea viajan escapados. Con 2200 el corte por longitud llegaba como
        # JSON incompleto, indistinguible de uno malformado —ningún proveedor
        # expone finish_reason—, así que se reintentaba con el mismo techo y se
        # acababa en el borrador local.
        max_tokens=4000,
        # La conexión del asistente debe usar un modelo rápido. Minuto y medio
        # cubre colas/cold starts de NIM sin dejar la interfaz bloqueada.
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
            provider_error_code = ""
            last_progress: Dict[str, Any] = {}
            async for chunk in stream_chat(builder_agent, conn, attempt_history, None):
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
                    # El modelo devuelve un único JSON, así que los tokens
                    # crudos no son mostrables: se informa la fase y el mensaje
                    # visible que ya haya llegado, y sólo cuando cambian.
                    progress = partial_progress(partial_reply)
                    if progress != last_progress:
                        last_progress = progress
                        yield _sse({"type": "progress", **progress})
                elif event.get("type") == "error":
                    provider_error = str(event.get("message") or "")
                    provider_error_code = str(event.get("code") or "")
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
                is_transient = _is_transient_provider_error(
                    provider_error_code, provider_error
                )
                if attempt == 0 and is_transient:
                    yield _sse({"type": "progress"})
                    # Volver en el mismo instante es contraproducente justo con
                    # el error que más se beneficia de esperar: un 429 lo emite
                    # un proveedor que pide precisamente eso. No hay retroceso
                    # exponencial porque solo hay un reintento, así que una
                    # constante es toda la curva que cabe; el keep-alive del
                    # SSE va a 10 s y no se resiente.
                    await asyncio.sleep(_ESPERA_ANTES_DE_REINTENTAR)
                    continue
                if force_ready or is_transient:
                    logger.warning(
                        "Agent builder provider unavailable; using local fallback: %s",
                        provider_error,
                    )
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
                        "code": provider_error_code,
                        "message": provider_error,
                    }
                )
                return

            if attempt == 0:
                attempt_history = [
                    *history,
                    *([{"role": "assistant", "content": reply}] if reply else []),
                    {
                        "role": "user",
                        "content": (
                            "Corrige tu respuesta anterior. No hagas más preguntas. "
                            "Diseña el agente solicitado y devuelve únicamente el "
                            'objeto JSON completo con status="ready" y draft.'
                        ),
                    },
                ]
                yield _sse({"type": "progress"})

        if force_ready:
            fallback = build_fallback_ready(body.messages, body.resources, body.mode)
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
