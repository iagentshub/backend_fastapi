"""Rutas de agentes: CRUD, exportación y chat SSE."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.api.routes.auth import require_auth
from app.config.data import AGENTS_DIR, CONN_FILE, MEMORY_DIR, SKILLS_DIR
from app.middleware.locale import get_locale
from app.models.agent import Agent
from app.services.chat import auto_update_memory, stream_chat
from app.storage.storage import AgentStorage, ConnectionStorage, MemoryStorage, SkillStorage

router = APIRouter(prefix="/api/agents", tags=["agents"])

_agents  = AgentStorage(AGENTS_DIR)
_conns   = ConnectionStorage(CONN_FILE)
_skills  = SkillStorage(SKILLS_DIR)
_memory  = MemoryStorage(MEMORY_DIR)

_VALID_SCOPES = {"public", "private", "all"}
_LOCALE_FIELDS = ("name", "description", "system_prompt")


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise HTTPException(status_code=400, detail="Scope no válido")


def _apply_locale(agent: Dict[str, Any], locale: str) -> Dict[str, Any]:
    """Overlay locale-specific fields from locale.<lang>.json if present."""
    if not agent:
        return agent
    scope = agent.get("scope", "public")
    agent_id = agent.get("id", "")
    locale_path = AGENTS_DIR / scope / agent_id / f"locale.{locale}.json"
    if not locale_path.exists() and locale != "es":
        locale_path = AGENTS_DIR / scope / agent_id / "locale.es.json"
    if locale_path.exists():
        try:
            overrides = json.loads(locale_path.read_text(encoding="utf-8"))
            for field in _LOCALE_FIELDS:
                if field in overrides:
                    agent = {**agent, field: overrides[field]}
        except Exception:
            pass
    return agent


@router.get("")
async def list_agents(scope: str = "all", _: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    _check_scope(scope)
    locale = get_locale()
    return [_apply_locale(a, locale) for a in _agents.list(scope)]


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
    return _apply_locale(a, get_locale())


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
    a = _apply_locale(a, get_locale())

    # Inject skills into system_prompt before export
    skills_text = ""
    for sid in (a.get("skills") or []):
        for scope in ("public", "private"):
            sk = _skills.get(scope, sid)
            if sk:
                skills_text += f"\n\n## Skill: {sk.get('name', sid)}\n{sk.get('content', '')}"
                break
    if skills_text:
        a = {**a, "system_prompt": (str(a.get("system_prompt") or "").strip() + skills_text).strip()}

    agent_obj = Agent.from_dict(a)
    try:
        content, media, filename = agent_obj.export(fmt)
    except NotImplementedError:
        raise HTTPException(status_code=400, detail=f"Formato '{fmt}' no soportado para tipo '{agent_obj.agent_type}'")

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
    a = _apply_locale(a, get_locale())

    body = await request.json()
    history: List[Dict[str, Any]] = body.get("messages") or []

    conn = _conns.get(a.get("connection_id") or "")
    if not conn:
        raise HTTPException(status_code=422, detail="El agente no tiene conexión configurada")

    from starlette.background import BackgroundTask

    done_event: List[dict] = []

    async def _gen():
        async for chunk in stream_chat(a, conn, history, _skills, _memory):
            yield chunk
            if chunk.startswith("data: "):
                try:
                    ev = json.loads(chunk[6:].strip())
                    if ev.get("type") == "done":
                        done_event.append(ev)
                except Exception:
                    pass

    async def _on_done():
        if not done_event:
            return
        ev = done_event[0]
        tokens = ev.get("tokens") or {}
        tok_in = int(tokens.get("in") or 0)
        tok_out = int(tokens.get("out") or 0)
        conn_id = conn.get("id") or ""
        if conn_id and (tok_in or tok_out):
            _conns.add_tokens(conn_id, tok_in, tok_out)
        if a.get("use_memory"):
            await auto_update_memory(a, conn, history, ev.get("reply", ""), _memory)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(_on_done),
    )
