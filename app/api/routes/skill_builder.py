"""AI-assisted skill builder routes."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import GroupContext, require_group
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.config.providers import PROVIDER_DEFAULT_MODELS
from app.models.agent import Agent
from app.services.builder_progress import partial_progress
from app.services.chat import stream_chat
from app.services.skill_builder import (
    SkillBuilderMessage,
    SkillBuilderMode,
    build_fallback_ready,
    build_from_skill_markdown,
    build_system_prompt,
    parse_builder_reply,
    should_force_ready,
)
from app.storage.groups import GroupStorage
from app.storage.guest import get_session, is_guest
from app.storage.storage import ConnectionStorage

router = APIRouter(prefix="/api/skill-builder", tags=["skill-builder"])
logger = logging.getLogger(__name__)
_conns = ConnectionStorage(DB_FILE)
_groups = GroupStorage(DB_FILE)


class SkillBuilderChatBody(BaseModel):
    connection_id: str = Field(min_length=1, max_length=300)
    messages: List[SkillBuilderMessage] = Field(min_length=1, max_length=30)
    mode: SkillBuilderMode = "guided"


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def builder_chat(
    body: SkillBuilderChatBody,
    ctx: GroupContext = Depends(require_group),
) -> StreamingResponse:
    user, group_id = ctx.user, ctx.group_id
    raw_conn_id = body.connection_id
    if "::" in raw_conn_id:
        conn_id, ollama_model = raw_conn_id.split("::", 1)
    else:
        conn_id, ollama_model = raw_conn_id, None

    role = ""
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
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para usar esta conexión directamente",
            )
        if role == "admin":
            conn = await _conns.get(conn_id, None)
        else:
            from app.api.routes.connections import _get_conn_any

            conn = await _get_conn_any(conn_id, user, group_id)

    if conn and ollama_model:
        conn = {**conn, "model": ollama_model}
    if not conn:
        raise HTTPException(
            status_code=404,
            detail="La conexión seleccionada no existe o no está disponible",
        )

    imported = build_from_skill_markdown(body.messages)
    if imported:
        async def imported_event():
            yield _sse(
                {"type": "builder_done", **imported.model_dump(mode="json")}
            )

        return StreamingResponse(imported_event(), media_type="text/event-stream")

    force_ready = should_force_ready(body.messages, body.mode)
    builder_conn = conn
    if str(conn.get("type") or "").lower() == "nvidia":
        builder_conn = {**conn, "model": PROVIDER_DEFAULT_MODELS["nvidia"]}

    builder_agent = Agent(
        id="_skill_builder",
        name="Constructor de skills",
        model=str(builder_conn.get("model") or ""),
        system_prompt=build_system_prompt(force_ready=force_ready, mode=body.mode),
        temperature=0.2,
        max_tokens=1400,
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
            last_progress: Dict[str, Any] = {}
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
                    # Mismo criterio que el constructor de agentes: la salida es
                    # un único JSON, así que se informa la fase y el mensaje
                    # visible ya recibido, y sólo cuando cambian.
                    progress = partial_progress(partial_reply)
                    if progress != last_progress:
                        last_progress = progress
                        yield _sse({"type": "progress", **progress})
                elif event.get("type") == "error":
                    provider_error = str(event.get("message") or "")
                    break
                elif event.get("type") == "done":
                    reply = str(event.get("reply") or "")

            candidate = reply or partial_reply
            if candidate:
                try:
                    envelope = parse_builder_reply(candidate)
                except ValueError as exc:
                    last_issue = str(exc)
                    logger.warning(
                        "Skill builder returned invalid structured output (%s chars): %s",
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
                    last_issue = "El modelo preguntó cuando ya debía crear la skill"

            if provider_error:
                last_issue = provider_error
                is_timeout = any(
                    marker in provider_error.lower()
                    for marker in ("timed out", "timeout")
                )
                if attempt == 0 and is_timeout:
                    yield _sse({"type": "progress"})
                    continue
                if force_ready:
                    fallback = build_fallback_ready(body.messages)
                    yield _sse(
                        {
                            "type": "builder_done",
                            **fallback.model_dump(mode="json"),
                        }
                    )
                    return
                yield _sse({"type": "error", "message": provider_error})
                return

            if attempt == 0:
                attempt_history = [
                    *history,
                    *([{"role": "assistant", "content": reply}] if reply else []),
                    {
                        "role": "user",
                        "content": (
                            "Corrige la respuesta. No hagas preguntas. Devuelve solo "
                            "el JSON completo con status=\"ready\" y una skill operativa."
                        ),
                    },
                ]
                yield _sse({"type": "progress"})

        if force_ready:
            fallback = build_fallback_ready(body.messages)
            yield _sse(
                {"type": "builder_done", **fallback.model_dump(mode="json")}
            )
            return
        yield _sse(
            {
                "type": "error",
                "message": f"No se pudo producir una skill válida. Detalle: {last_issue}",
            }
        )

    return StreamingResponse(generate(), media_type="text/event-stream")
