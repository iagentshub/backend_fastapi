"""AI-assisted agent builder routes."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import WorkspaceContext, require_workspace
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.models.agent import Agent
from app.services.agent_builder import (
    BuilderMode,
    BuilderMessage,
    BuilderResources,
    build_fallback_ready,
    build_system_prompt,
    can_build_without_model,
    guided_recovery_question,
    parse_builder_reply,
    should_force_ready,
)
from app.services.chat import stream_chat
from app.storage.guest import get_session, is_guest
from app.storage.storage import ConnectionStorage
from app.storage.workspaces import WorkspaceStorage

router = APIRouter(prefix="/api/agent-builder", tags=["agent-builder"])
_conns = ConnectionStorage(DB_FILE)
_workspaces = WorkspaceStorage(DB_FILE)


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
    ctx: WorkspaceContext = Depends(require_workspace),
) -> StreamingResponse:
    user, workspace_id = ctx.user, ctx.workspace_id
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
            workspace_id != user
            and role != "admin"
            and not await _workspaces.has_resource_permission(
                workspace_id, user, "connections", conn_id, "direct"
            )
        ):
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para usar esta conexión directamente",
            )
    if not is_guest(user) and role == "admin":
        conn = await _conns.get(conn_id, None)
    elif not is_guest(user):
        from app.api.routes.connections import _get_conn_any

        conn = await _get_conn_any(conn_id, user, workspace_id)
    if conn and ollama_model:
        conn = {**conn, "model": ollama_model}
    if not conn:
        raise HTTPException(
            status_code=404,
            detail="La conexión seleccionada no existe o no está disponible",
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
        # Un borrador completo cabe holgadamente aquí. Evita que un modelo
        # pequeño divague durante miles de tokens antes de cerrar el JSON.
        max_tokens=1_200,
        # La conexión del asistente debe usar un modelo rápido. Tres minutos
        # cubren colas/cold starts de NIM sin dejar la interfaz bloqueada.
        timeout=180,
    )
    history = [message.model_dump() for message in body.messages]

    async def generate():
        reply = ""
        async for chunk in stream_chat(builder_agent, conn, history, None):
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
                # Keep the browser request active without exposing partial JSON.
                yield _sse({"type": "progress"})
            elif event.get("type") == "error":
                yield _sse(event)
                return
            elif event.get("type") == "done":
                reply = str(event.get("reply") or "")
        if not reply:
            yield _sse({"type": "error", "message": "El proveedor no devolvió respuesta"})
            return
        try:
            envelope = parse_builder_reply(reply, body.resources)
        except ValueError as exc:
            if force_ready:
                envelope = build_fallback_ready(
                    body.messages, body.resources, body.mode
                )
            elif body.mode == "guided":
                envelope = guided_recovery_question(body.messages)
            else:
                yield _sse({"type": "error", "message": str(exc)})
                return
        if body.mode == "guided" and envelope.status == "collecting":
            # Los modelos pequeños tienden a agrupar varias preguntas. La ruta
            # guiada garantiza una sola pregunta breve y predecible por turno.
            envelope = guided_recovery_question(body.messages)
        if force_ready and envelope.status != "ready":
            envelope = build_fallback_ready(
                body.messages, body.resources, body.mode
            )
        yield _sse(
            {
                "type": "builder_done",
                **envelope.model_dump(mode="json"),
            }
        )

    return StreamingResponse(generate(), media_type="text/event-stream")
