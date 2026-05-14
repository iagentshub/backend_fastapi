"""Rutas de agentes: CRUD, exportación y chat SSE."""
from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, DB_FILE, MEMORY_DIR, SKILLS_DIR
from app.config.session import RATE_CHAT_CALLS, RATE_CHAT_WINDOW
from app.middleware.locale import get_locale
from app.middleware.ratelimit import RateLimiter
from app.models.agent import Agent
from app.services.chat import auto_update_memory, stream_chat
from app.storage.chat import ChatStorage
from app.storage.guest import GuestKnowledgeAdapter, GuestMemoryAdapter, get_session, is_guest
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import AgentStorage, ConnectionStorage, MemoryStorage, SkillStorage

router = APIRouter(prefix="/api/agents", tags=["agents"])

_agents       = AgentStorage(AGENTS_DIR)
_conns        = ConnectionStorage(DB_FILE)
_skills       = SkillStorage(SKILLS_DIR)
_memory       = MemoryStorage(MEMORY_DIR)
_chat         = ChatStorage(DB_FILE)
_knowledge    = KnowledgeStorage(DB_FILE)
_chat_limiter = RateLimiter(calls=RATE_CHAT_CALLS, window=RATE_CHAT_WINDOW)


def _conn_owner(user: str) -> str | None:
    return None if get_user_role(user) == "admin" else user

_VALID_SCOPES = {"public", "private", "all"}
_LOCALE_FIELDS = ("name", "description", "system_prompt")


def _name_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]", "-", name.lower().strip())
    return re.sub(r"-{2,}", "-", slug).strip("-") or "agent"


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
async def list_agents(scope: str = "all", user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    _check_scope(scope)
    locale = get_locale()
    if is_guest(user):
        s = get_session(user)
        public = _agents.list("public") if scope in ("public", "all") else []
        private = s.agents if scope in ("private", "all") else []
        return [_apply_locale(a, locale) for a in public + private]
    return [_apply_locale(a, locale) for a in _agents.list(scope)]


@router.post("")
async def save_agent(
    request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    payload = await request.json()
    scope = str(payload.pop("scope", "private") or "private")
    if scope not in ("public", "private"):
        raise HTTPException(status_code=400, detail="Scope no válido")
    if is_guest(user):
        s = get_session(user)
        agent: Dict[str, Any] = {**payload, "id": payload.get("id") or uuid4().hex[:12], "scope": "private"}
        s.agents = [a for a in s.agents if a.get("id") != agent["id"]]
        s.agents.append(agent)
        return agent
    try:
        return _agents.save(payload, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        s = get_session(user)
        a = next((a for a in s.agents if a.get("id") == agent_id), None) or _agents.get(agent_id, scope="public")
        if not a:
            raise HTTPException(status_code=404, detail="Agente no encontrado")
        return _apply_locale(a, get_locale())
    a = _agents.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    return _apply_locale(a, get_locale())


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        s = get_session(user)
        before = len(s.agents)
        s.agents = [a for a in s.agents if a.get("id") != agent_id]
        if len(s.agents) == before:
            raise HTTPException(status_code=404, detail="Agente no encontrado")
        return {"ok": True}
    try:
        if not _agents.delete(agent_id):
            raise HTTPException(status_code=404, detail="Agente no encontrado")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


@router.get("/{agent_id}/export/{fmt}")
async def export_agent(
    agent_id: str, fmt: str, user: str = Depends(require_auth)
) -> Response:
    """fmt: openai | claude | github"""
    if is_guest(user):
        s = get_session(user)
        a = next((ag for ag in s.agents if ag.get("id") == agent_id), None) or _agents.get(agent_id, scope="public")
        memory_store = GuestMemoryAdapter(s)
    else:
        a = _agents.get(agent_id)
        memory_store = _memory
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    a = _apply_locale(a, get_locale())

    # For non-Claude formats inject skills into system_prompt.
    # Claude format ships skills as separate .claude/commands/ files instead.
    if fmt != "claude":
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

    if fmt == "claude":
        slug = _name_slug(agent_obj.name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f".claude/agents/{slug}.md", content)
            for sid in (a.get("skills") or []):
                for scope in ("public", "private"):
                    sk = _skills.get(scope, sid)
                    if sk:
                        sk_name = sk.get("name", sid)
                        sk_slug = _name_slug(sk_name)
                        sk_desc = str(sk.get("description") or "")[:200]
                        skill_md = (
                            f"---\nname: {sk_name}\ndescription: {sk_desc}\n---\n\n"
                            f"{sk.get('content', '')}"
                        )
                        # Embed as Claude Code command (flat usage inside project)
                        zf.writestr(f".claude/commands/{sk_slug}/Skill.md", skill_md)
                        # Also embed as a ready-to-import skill ZIP (native claude.ai format)
                        skill_zip_buf = io.BytesIO()
                        with zipfile.ZipFile(skill_zip_buf, "w", zipfile.ZIP_DEFLATED) as szf:
                            szf.writestr(f"{sk_slug}/Skill.md", skill_md)
                        zf.writestr(f"skills/{sk_slug}.zip", skill_zip_buf.getvalue())
                        break
            mem_file = a.get("memory_file") or f"{agent_id}.md"
            mem_content = memory_store.get(mem_file)
            if mem_content and mem_content.strip():
                zf.writestr(".claude/CLAUDE.md", mem_content.strip())
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{slug}-claude.zip"'},
        )

    if fmt == "github":
        slug = _name_slug(agent_obj.name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f".github/agents/{slug}.md", content)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{slug}-copilot.zip"'},
        )

    return Response(
        content=content.encode("utf-8"),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{agent_id}/chat")
async def chat(
    agent_id: str,
    request: Request,
    user: str = Depends(require_auth),
    _rl: None = Depends(_chat_limiter),
) -> StreamingResponse:
    if is_guest(user):
        s = get_session(user)
        a = next((a for a in s.agents if a.get("id") == agent_id), None) or _agents.get(agent_id, scope="public")
    else:
        a = _agents.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    a = _apply_locale(a, get_locale())

    body = await request.json()
    history: List[Dict[str, Any]] = body.get("messages") or []
    conversation_id: str = str(body.get("conversation_id") or "").strip()

    if is_guest(user):
        s = get_session(user)
        conn_id = a.get("connection_id") or ""
        conn = next((c for c in s.connections if c.get("id") == conn_id), None)
        memory_store = GuestMemoryAdapter(s)
        knowledge_store = GuestKnowledgeAdapter(s)
    else:
        conn = _conns.get(a.get("connection_id") or "", _conn_owner(user))
        memory_store = _memory
        knowledge_store = _knowledge

    if not conn:
        raise HTTPException(status_code=422, detail="El agente no tiene conexión configurada")

    from starlette.background import BackgroundTask

    done_event: List[dict] = []

    async def _gen():
        async for chunk in stream_chat(a, conn, history, _skills, memory_store, knowledge_store):
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
        if not is_guest(user):
            conn_id = conn.get("id") or ""
            if conn_id and (tok_in or tok_out):
                _conns.add_tokens(conn_id, tok_in, tok_out)
            if conversation_id:
                reply = ev.get("reply", "")
                user_msg = next(
                    (m for m in reversed(history) if m.get("role") == "user"), None
                )
                if user_msg:
                    _chat.add_message(conversation_id, "user", str(user_msg.get("content") or ""))
                if reply:
                    _chat.add_message(conversation_id, "assistant", reply)
                    title = str(user_msg.get("content") or "")[:80] if user_msg else ""
                    _chat.touch_conversation(conversation_id, title)
        if a.get("use_memory"):
            await auto_update_memory(a, conn, history, ev.get("reply", ""), memory_store)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(_on_done),
    )
