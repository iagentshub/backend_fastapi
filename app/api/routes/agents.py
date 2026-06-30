"""Rutas de agentes: CRUD, exportación y chat SSE."""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.routes.auth import WorkspaceContext, require_workspace
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, DB_FILE, MEMORY_DIR, SKILLS_DIR
from app.config.session import RATE_CHAT_CALLS, RATE_CHAT_WINDOW
from app.middleware.locale import get_locale
from app.middleware.ratelimit import RateLimiter
from app.models.agent import Agent
from app.services.chat import auto_update_memory, stream_chat
from app.storage.chat import ChatStorage

from app.storage.guest import (
    GuestKnowledgeAdapter,
    GuestMemoryAdapter,
    get_session,
    is_guest,
)
from app.storage.knowledge import FolderStorage, KnowledgeStorage
from app.storage.storage import (
    AgentStorage,
    ConnectionStorage,
    MemoryStorage,
    SkillStorage,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])

_agents = AgentStorage(AGENTS_DIR)
_conns = ConnectionStorage(DB_FILE)
_skills = SkillStorage(SKILLS_DIR)
_memory = MemoryStorage(MEMORY_DIR)
_chat = ChatStorage(DB_FILE)
_knowledge = KnowledgeStorage(DB_FILE)
_folders = FolderStorage(DB_FILE)
_chat_limiter = RateLimiter(calls=RATE_CHAT_CALLS, window=RATE_CHAT_WINDOW)


class _AgentFolderMove(BaseModel):
    folder_id: Optional[str] = None


async def _conn_owner(user: str) -> str | None:
    return None if await get_user_role(user) == "admin" else user


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
async def list_agents(
    scope: str = "all",
    label: Optional[str] = None,
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    _check_scope(scope)
    locale = get_locale()
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        s = get_session(user)
        public = await _agents.list("public") if scope in ("public", "all") else []
        private = s.agents if scope in ("private", "all") else []
        items = public + private
        if label:
            items = [a for a in items if label in (a.get("labels") or [])]
        if offset:
            items = items[offset:]
        if limit:
            items = items[:limit]
        return [_apply_locale(a, locale) for a in items]
    agents = await _agents.list(scope)
    role = await get_user_role(user)
    if role not in ("admin", "guest"):
        from app.storage.groups import GroupStorage

        gs = GroupStorage(DB_FILE)
        own = [a for a in agents if a.get("owner_id") == workspace_id]
        shared_ids = set(
            await gs.get_user_shared_resource_ids(user, "agent", workspace_id)
        )
        own_ids = {a["id"] for a in own}
        extra = [a for a in agents if a["id"] in (shared_ids - own_ids)]
        for a in extra:
            a["_shared"] = True
        agents = own + extra
    if label:
        agents = [a for a in agents if label in (a.get("labels") or [])]
    if offset:
        agents = agents[offset:]
    if limit:
        agents = agents[:limit]
    return [_apply_locale(a, locale) for a in agents]


@router.post("")
async def save_agent(
    request: Request, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    payload = await request.json()
    scope = str(payload.pop("scope", "private") or "private")
    if scope not in ("public", "private"):
        raise HTTPException(status_code=400, detail="Scope no válido")
    if is_guest(user):
        s = get_session(user)
        agent: Dict[str, Any] = {
            **payload,
            "id": payload.get("id") or uuid4().hex[:12],
            "scope": "private",
        }
        s.agents = [a for a in s.agents if a.get("id") != agent["id"]]
        s.agents.append(agent)
        return agent
    try:
        return await _agents.save(payload, scope, owner_id=workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user = ctx.user
    if is_guest(user):
        s = get_session(user)
        a = next(
            (a for a in s.agents if a.get("id") == agent_id), None
        ) or await _agents.get(agent_id, scope="public")
        if not a:
            raise HTTPException(status_code=404, detail="Agente no encontrado")
        return _apply_locale(a, get_locale())
    a = await _agents.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    return _apply_locale(a, get_locale())


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str, ctx: WorkspaceContext = Depends(require_workspace)
) -> Dict[str, Any]:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        s = get_session(user)
        before = len(s.agents)
        s.agents = [a for a in s.agents if a.get("id") != agent_id]
        if len(s.agents) == before:
            raise HTTPException(status_code=404, detail="Agente no encontrado")
        return {"ok": True}
    a = await _agents.get(agent_id)
    if a and await get_user_role(user) != "admin" and a.get("owner_id") != workspace_id:
        raise HTTPException(
            status_code=403, detail="No tienes permiso para eliminar este agente"
        )
    try:
        if not await _agents.delete(agent_id):
            raise HTTPException(status_code=404, detail="Agente no encontrado")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


@router.patch("/{agent_id}/folder")
async def move_agent_folder(
    agent_id: str,
    body: _AgentFolderMove,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    if is_guest(ctx.user):
        raise HTTPException(
            status_code=403, detail="Los invitados no pueden mover agentes"
        )
    if not await _agents.move_folder(agent_id, body.folder_id):
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    return {"ok": True}


@router.get("/{agent_id}/export/{fmt}")
async def export_agent(
    agent_id: str, fmt: str, ctx: WorkspaceContext = Depends(require_workspace)
) -> Response:
    """fmt: openai | claude | github | mcp"""
    user = ctx.user
    if is_guest(user):
        s = get_session(user)
        a = next(
            (ag for ag in s.agents if ag.get("id") == agent_id), None
        ) or await _agents.get(agent_id, scope="public")
        memory_store = GuestMemoryAdapter(s)
        knowledge_store: Any = GuestKnowledgeAdapter(s)
    else:
        a = await _agents.get(agent_id)
        memory_store = _memory
        knowledge_store = _knowledge
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    a = _apply_locale(a, get_locale())

    # Resolve skills
    resolved_skills: List[Dict[str, Any]] = []
    for sid in a.get("skills") or []:
        for scope in ("public", "private"):
            sk = await _skills.get(scope, sid)
            if sk:
                resolved_skills.append(sk)
                break

    # Resolve knowledge items
    resolved_knowledge: List[Dict[str, Any]] = []
    for kid in a.get("knowledge") or []:
        try:
            item = await knowledge_store.get(kid)
            if item:
                resolved_knowledge.append(item)
        except Exception:
            pass

    # Resolve memory
    mem_file = a.get("memory_file") or f"{agent_id}.md"
    if is_guest(user):
        mem_content = (await memory_store.get(mem_file) or "").strip()
    else:
        mem_content = (await memory_store.get(mem_file, owner_id=user) or "").strip()

    # OpenAI: inject skills as text into system_prompt.
    if fmt == "openai":
        skills_text = "".join(
            f"\n\n## Skill: {sk.get('name', '')}\n{sk.get('content', '')}"
            for sk in resolved_skills
        )
        if skills_text:
            a = {
                **a,
                "system_prompt": (
                    str(a.get("system_prompt") or "").strip() + skills_text
                ).strip(),
            }

    # MCP: pass resolved skills for tool generation.
    if fmt == "mcp":
        a = {**a, "_resolved_skills": resolved_skills}

    agent_obj = Agent.from_dict(a)
    try:
        content, media, filename = agent_obj.export(fmt)
    except NotImplementedError:
        raise HTTPException(
            status_code=400,
            detail=f"Formato '{fmt}' no soportado para tipo '{agent_obj.agent_type}'",
        )

    slug = _name_slug(agent_obj.name)

    def _skill_md(sk: Dict[str, Any]) -> str:
        sk_name = sk.get("name", "")
        sk_desc = str(sk.get("description") or "")[:200]
        return f"---\nname: {sk_name}\ndescription: {sk_desc}\n---\n\n{sk.get('content', '')}"

    def _knowledge_md(item: Dict[str, Any]) -> str:
        title = item.get("title") or "item"
        source = item.get("source") or ""
        ktype = item.get("type") or "text"
        body = item.get("content") or ""
        header = f"# {title}\n\n"
        if source:
            header += f"> Source: {source}  \n> Type: {ktype}\n\n"
        return header + body

    def _add_knowledge(zf: zipfile.ZipFile, prefix: str = "knowledge/") -> None:
        for item in resolved_knowledge:
            kslug = _name_slug(item.get("title") or "item")
            zf.writestr(f"{prefix}{kslug}.md", _knowledge_md(item))

    if fmt == "claude":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f".claude/agents/{slug}.md", content)
            for sk in resolved_skills:
                sk_slug = _name_slug(sk.get("name", ""))
                skill_md = _skill_md(sk)
                zf.writestr(f".claude/skills/{sk_slug}/SKILL.md", skill_md)
                skill_zip_buf = io.BytesIO()
                with zipfile.ZipFile(skill_zip_buf, "w", zipfile.ZIP_DEFLATED) as szf:
                    szf.writestr(f"{sk_slug}/SKILL.md", skill_md)
                zf.writestr(f"skills/{sk_slug}.zip", skill_zip_buf.getvalue())
            if mem_content:
                zf.writestr(".claude/CLAUDE.md", mem_content)
            _add_knowledge(zf)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}-claude.zip"'
            },
        )

    if fmt == "github":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f".github/agents/{slug}.md", content)
            for sk in resolved_skills:
                sk_slug = _name_slug(sk.get("name", ""))
                zf.writestr(f".github/skills/{sk_slug}/SKILL.md", _skill_md(sk))
            if mem_content:
                zf.writestr(".github/COPILOT_INSTRUCTIONS.md", mem_content)
            _add_knowledge(zf)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}-copilot.zip"'
            },
        )

    if fmt == "openai":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("agent.json", content)
            if mem_content:
                zf.writestr("memory.md", mem_content)
            _add_knowledge(zf)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}-openai.zip"'
            },
        )

    if fmt == "mcp":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{slug}-server.py", content)
            if mem_content:
                zf.writestr("memory.md", mem_content)
            _add_knowledge(zf)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{slug}-mcp.zip"'},
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
    ctx: WorkspaceContext = Depends(require_workspace),
    _rl: None = Depends(_chat_limiter),
) -> StreamingResponse:
    user, workspace_id = ctx.user, ctx.workspace_id
    if is_guest(user):
        s = get_session(user)
        a = next(
            (a for a in s.agents if a.get("id") == agent_id), None
        ) or await _agents.get(agent_id, scope="public")
    else:
        a = await _agents.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    role = await get_user_role(user)
    a = _apply_locale(a, get_locale())

    body = await request.json()
    history: List[Dict[str, Any]] = body.get("messages") or []
    conversation_id: str = str(body.get("conversation_id") or "").strip()

    # Ollama virtual connections: "base_id::model_name"
    raw_conn_id = a.get("connection_id") or ""
    if "::" in raw_conn_id:
        base_conn_id, ollama_model = raw_conn_id.split("::", 1)
    else:
        base_conn_id, ollama_model = raw_conn_id, None

    if is_guest(user):
        s = get_session(user)
        conn = next((c for c in s.connections if c.get("id") == base_conn_id), None)
        memory_store = GuestMemoryAdapter(s)
        knowledge_store = GuestKnowledgeAdapter(s)
    else:
        conn_id = base_conn_id
        conn = await _conns.get(conn_id, None if role == "admin" else workspace_id)
        if not conn:
            conn = await _conns.get(conn_id, user)
        memory_store = _memory
        knowledge_store = _knowledge

    if conn and ollama_model:
        conn = {**conn, "model": ollama_model}

    if not conn:
        raise HTTPException(
            status_code=422, detail="El agente no tiene conexión configurada"
        )

    from starlette.background import BackgroundTask

    done_event: List[dict] = []

    async def _gen():
        async for chunk in stream_chat(
            a, conn, history, _skills, memory_store, knowledge_store
        ):
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
            if base_conn_id and (tok_in or tok_out):
                await _conns.add_tokens(base_conn_id, tok_in, tok_out)
            if (tok_in or tok_out) and a.get("scope", "private") == "private":
                await _agents.add_tokens(agent_id, tok_in, tok_out)
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
                    await _chat.add_message(conversation_id, "assistant", reply)
                    title = str(user_msg.get("content") or "")[:80] if user_msg else ""
                    await _chat.touch_conversation(conversation_id, title)
        if a.get("use_memory"):
            await auto_update_memory(
                a, conn, history, ev.get("reply", ""), memory_store
            )

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(_on_done),
    )
