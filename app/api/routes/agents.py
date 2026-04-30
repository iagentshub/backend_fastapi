"""Rutas de agentes: CRUD, exportación y chat SSE."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.api.routes.auth import require_auth
from app.config.data import AGENTS_DIR, CONN_FILE, MEMORY_DIR, SKILLS_DIR
from app.services.chat import stream_chat
from app.storage.storage import AgentStorage, ConnectionStorage, MemoryStorage, SkillStorage

router = APIRouter(prefix="/api/agents", tags=["agents"])

_agents  = AgentStorage(AGENTS_DIR)
_conns   = ConnectionStorage(CONN_FILE)
_skills  = SkillStorage(SKILLS_DIR)
_memory  = MemoryStorage(MEMORY_DIR)

_VALID_SCOPES = {"public", "private", "all"}


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise HTTPException(status_code=400, detail="Scope no válido")


@router.get("")
async def list_agents(scope: str = "all", _: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    _check_scope(scope)
    return _agents.list(scope)


@router.post("")
async def save_agent(
    request: Request, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    payload = await request.json()
    scope = str(payload.pop("scope", "private") or "private")
    if scope not in ("public", "private"):
        raise HTTPException(status_code=400, detail="Scope no válido")
    try:
        return _agents.save(payload, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    a = _agents.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    return a


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    try:
        if not _agents.delete(agent_id):
            raise HTTPException(status_code=404, detail="Agente no encontrado")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


@router.get("/{agent_id}/export/{fmt}")
async def export_agent(
    agent_id: str, fmt: str, _: str = Depends(require_auth)
) -> Response:
    """fmt: openai | claude | github"""
    a = _agents.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")

    skills_text = ""
    for sid in (a.get("skills") or []):
        for scope in ("public", "private"):
            sk = _skills.get(scope, sid)
            if sk:
                skills_text += f"\n\n## Skill: {sk.get('name', sid)}\n{sk.get('content', '')}"
                break

    system = str(a.get("system_prompt") or "").strip()
    full_prompt = f"{system}{skills_text}".strip()
    name = a.get("name", agent_id)

    if fmt == "openai":
        payload = {
            "model": a.get("model") or "gpt-4o",
            "name": name,
            "description": a.get("description") or "",
            "instructions": full_prompt,
            "temperature": a.get("temperature", 0.7),
        }
        if a.get("max_tokens"):
            payload["max_response_output_tokens"] = a["max_tokens"]
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        media = "application/json"
        filename = f"{agent_id}-openai.json"

    elif fmt == "claude":
        payload = {
            "name": name,
            "description": a.get("description") or "",
            "model": a.get("model") or "claude-sonnet-4-5",
            "system_prompt": full_prompt,
            "temperature": a.get("temperature", 0.7),
        }
        if a.get("max_tokens"):
            payload["max_tokens"] = a["max_tokens"]
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        media = "application/json"
        filename = f"{agent_id}-claude.json"

    elif fmt == "github":
        lines = [f"# {name}", "", a.get("description") or "", "", full_prompt]
        content = "\n".join(lines).strip()
        media = "text/markdown; charset=utf-8"
        filename = "copilot-instructions.md"

    else:
        raise HTTPException(status_code=400, detail=f"Formato '{fmt}' no soportado")

    return Response(
        content=content.encode("utf-8"),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{agent_id}/chat")
async def chat(
    agent_id: str, request: Request, _: str = Depends(require_auth)
) -> StreamingResponse:
    a = _agents.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")

    body = await request.json()
    history: List[Dict[str, Any]] = body.get("messages") or []

    conn = _conns.get(a.get("connection_id") or "")
    if not conn:
        raise HTTPException(status_code=422, detail="El agente no tiene conexión configurada")

    return StreamingResponse(
        stream_chat(a, conn, history, _skills, _memory),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
