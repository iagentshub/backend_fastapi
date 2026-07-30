"""Rutas de agentes: CRUD, exportación y chat SSE."""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.routes.auth import GroupContext, require_auth, require_group
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, DB_FILE, MEMORY_DIR, SKILLS_DIR
from app.config.session import RATE_CHAT_CALLS, RATE_CHAT_WINDOW
from app.errors import APIError
from app.middleware.locale import get_locale
from app.middleware.ratelimit import RateLimiter
from app.models.agent import Agent
from app.services.chat import stream_chat
from app.storage.chat import ChatStorage
from app.storage.db import IS_PG, PH, open_db
from app.storage.folders import FolderStorage
from app.storage.guest import (
    GuestKnowledgeAdapter,
    GuestMemoryAdapter,
    get_session,
    is_guest,
)
from app.storage.knowledge import KnowledgeStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.storage import (
    AgentStorage,
    ConnectionStorage,
    MemoryStorage,
    SkillStorage,
)
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.utils.origin import compute_origin_type

router = APIRouter(prefix="/api/agents", tags=["agents"])

_agents = AgentStorage(AGENTS_DIR)
_conns = ConnectionStorage(DB_FILE)
_skills = SkillStorage(SKILLS_DIR)
_memory = MemoryStorage(MEMORY_DIR)
_shares = GroupShareStorage(DB_FILE)
_groups = GroupStorage(DB_FILE)
_chat = ChatStorage(DB_FILE)
_knowledge = KnowledgeStorage(DB_FILE)
_folders = FolderStorage()
_versions = ResourceVersionStorage()
_chat_limiter = RateLimiter(calls=RATE_CHAT_CALLS, window=RATE_CHAT_WINDOW)


class _AgentPreferenceBody(BaseModel):
    connection_id: Optional[str] = None


async def _validate_resource_refs(
    payload: Dict[str, Any], user: str, group_id: str
) -> None:
    """Rechaza IDs de skills/knowledge que el usuario no puede legítimamente
    usar (ni son suyos, públicos, ni están compartidos con él).

    Sin esto, cualquiera puede adjuntar el ID de una skill o un knowledge
    ajenos a su propio agente y leer su contenido completo vía chat o export
    (mismo problema que ALTO-5/A1 en sharing.py, sin corregir aquí).
    """
    for sid in payload.get("skills") or []:
        skill = await _skills.get_any(sid)
        if not skill or skill.get("scope") == "public":
            continue
        if not await _shares.is_accessible(
            _groups,
            resource_type="skill",
            resource_id=sid,
            owner_id=skill.get("owner_id"),
            requester=user,
            requester_group=group_id,
        ):
            raise APIError(
                403,
                "forbidden",
                "No tienes acceso a una de las skills indicadas",
                extra={"resource": "skill", "id": sid},
            )
    for kid in payload.get("knowledge") or []:
        item = await _knowledge.get(kid)
        if not item:
            continue
        if not await _shares.is_accessible(
            _groups,
            resource_type="knowledge",
            resource_id=kid,
            owner_id=item.get("owner_id"),
            requester=user,
            requester_group=group_id,
        ):
            raise APIError(
                403,
                "forbidden",
                "No tienes acceso a uno de los elementos de conocimiento indicados",
                extra={"resource": "knowledge", "id": kid},
            )


async def _assert_can_read_agent(
    agent_id: str,
    agent: Dict[str, Any],
    ctx: GroupContext,
) -> None:
    """Lanza 403 si el usuario no tiene derecho de lectura sobre el agente.

    Acceso permitido cuando se cumple al menos una condición:
    - El agente es público (scope == "public")
    - owner_id coincide con el usuario o con su group activo
    - El usuario tiene rol "admin"
    - El agente está compartido con algún group al que pertenece el usuario
    """
    if agent.get("scope") == "public":
        return
    user = ctx.user
    group_id = ctx.group_id
    owner_id = agent.get("owner_id")
    if owner_id in (user, group_id):
        return
    if await get_user_role(user) == "admin":
        return
    # Comprobar shares activos con los groups del usuario
    user_groups = await _groups.list_for_user(user)
    if user_groups:
        group_ids = [g["id"] for g in user_groups]
        results = await asyncio.gather(
            *[
                _shares.get_group_shared_resource_ids(gid, "agent")
                for gid in group_ids
            ]
        )
        for shared_ids in results:
            if agent_id in shared_ids:
                return
    raise APIError(403, "forbidden", "No tienes acceso a este agente")


async def _conn_owner(user: str) -> str | None:
    return None if await get_user_role(user) == "admin" else user


_VALID_SCOPES = {"public", "private", "all"}
_LOCALE_FIELDS = ("name", "description", "system_prompt")


def _name_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]", "-", name.lower().strip())
    return re.sub(r"-{2,}", "-", slug).strip("-") or "agent"


def _check_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise APIError(400, "invalid_field", "Scope no válido", extra={"field": "scope"})


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
    owner_scope: str = "group",
    group_id: Optional[str] = None,
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    _check_scope(scope)
    locale = get_locale()
    user = ctx.user
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
        result: List[Dict[str, Any]] = []
        for a in items:
            a = _apply_locale(a, locale)
            a["origin_type"] = compute_origin_type(a)
            result.append(a)
        return result
    agents = await _agents.list(scope)
    role = await get_user_role(user)
    if group_id is not None:
        # Filtro por grupo: se aplica siempre, incluido admin
        if role != "admin" and not await _groups.can_access(group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        shared_ids = set(
            await _shares.get_group_shared_resource_ids(group_id, "agent")
        )
        agents = [a for a in agents if a["id"] in shared_ids]
        for a in agents:
            a["_shared"] = True
            a["_group_id"] = group_id
    else:
        # Vista por defecto: propios + recursos del group activo + compartidos
        # (incluye admin: la visibilidad global de admin se sirve vía /api/admin/agents,
        # no filtrar aquí exponía agentes privados de otros usuarios sin marcar como ajenos)
        # En group de equipo (group_id != user), owner_id puede ser el UUID del group.
        group_id = ctx.group_id
        own = [
            a
            for a in agents
            if a.get("owner_id") == user or a.get("owner_id") == group_id
        ]
        own_ids = {a["id"] for a in own}
        user_groups = await _groups.list_for_user(user)

        # Paralelizar todas las queries de shares (una por grupo) en lugar de N+1 serial
        if user_groups:
            group_ids = [g["id"] for g in user_groups]
            results = await asyncio.gather(
                *[
                    _shares.get_group_shared_resource_ids(gid, "agent")
                    for gid in group_ids
                ]
            )
            shared_map: Dict[
                str, str
            ] = {}  # resource_id -> group_id (primer grupo que lo comparte)
            for gid, rids in zip(group_ids, results):
                for rid in rids:
                    if rid not in shared_map:
                        shared_map[rid] = gid
        else:
            shared_map = {}

        # Paralelizar las consultas owner_is_active para agentes compartidos
        extra_candidates = [
            a for a in agents if a["id"] in (set(shared_map.keys()) - own_ids)
        ]
        if extra_candidates:
            unique_owners = list({a.get("owner_id") or "" for a in extra_candidates})
            active_results = await asyncio.gather(
                *[_groups.owner_is_active(oid) for oid in unique_owners]
            )
            active_owners = {
                oid for oid, ok in zip(unique_owners, active_results) if ok
            }
            extra = []
            for a in extra_candidates:
                if (a.get("owner_id") or "") in active_owners:
                    a["_shared"] = True
                    a["_group_id"] = shared_map[a["id"]]
                    extra.append(a)
        else:
            extra = []

        agents = own + extra
    if label:
        agents = [a for a in agents if label in (a.get("labels") or [])]
    if ctx.group_id != user and role != "admin":
        agents = [
            agent for agent in agents
            if await _groups.has_resource_permission(
                ctx.group_id, user, "agents", agent["id"], "use"
            )
        ]
    if offset:
        agents = agents[offset:]
    if limit:
        agents = agents[:limit]
    agents = await _folders.enrich_items(
        agents, default_owner=ctx.group_id, resource_type="agents"
    )
    enriched: List[Dict[str, Any]] = []
    for a in agents:
        a = _apply_locale(a, locale)
        a["origin_type"] = compute_origin_type(a)
        enriched.append(a)
    return enriched


@router.post("")
async def save_agent(
    request: Request, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    payload = await request.json()
    scope = str(payload.pop("scope", "private") or "private")
    if scope not in ("public", "private"):
        raise APIError(400, "invalid_field", "Scope no válido", extra={"field": "scope"})
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
    role = await get_user_role(user)
    # Restrict editing to owner: if payload has an existing ID owned by someone else, block it
    agent_id_in_payload = payload.get("id")
    if agent_id_in_payload:
        existing = await _agents.get(agent_id_in_payload)
        if (
            existing
            and existing.get("owner_id") is not None
            and existing.get("owner_id") != group_id
        ):
            if role != "admin":
                raise APIError(
                    403,
                    "forbidden",
                    "Solo el propietario puede editar este agente",
                )
    if role != "admin":
        await _validate_resource_refs(payload, user, group_id)
    try:
        folder_id = payload.pop("folder_id", None)
        saved = await _agents.save(payload, scope, owner_id=group_id)
        if folder_id is not None:
            try:
                await _folders.assign(group_id, "agents", saved["id"], folder_id or None)
            except ValueError as exc:
                raise APIError(422, "folder_resource_mismatch", str(exc)) from exc
        saved["folder_id"] = await _folders.folder_for(group_id, "agents", saved["id"])
        await _versions.create(
            "agent", saved["id"], group_id, saved, user, reason="save"
        )
        return saved
    except ValueError as e:
        raise APIError(422, "agent_invalid_payload", str(e))


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user = ctx.user
    if is_guest(user):
        s = get_session(user)
        a = next(
            (a for a in s.agents if a.get("id") == agent_id), None
        ) or await _agents.get(agent_id, scope="public")
        if not a:
            raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
        a = _apply_locale(a, get_locale())
        a["origin_type"] = compute_origin_type(a)
        return a
    a = await _agents.get(agent_id)
    if not a:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
    await _assert_can_read_agent(agent_id, a, ctx)
    if not await _groups.has_resource_permission(
        ctx.group_id, user, "agents", agent_id, "use"
    ):
        raise APIError(403, "forbidden", "Sin permiso para usar este agente")
    a = _apply_locale(a, get_locale())
    a["origin_type"] = compute_origin_type(a)
    owner = str(a.get("owner_id") or ctx.group_id)
    a["folder_id"] = await _folders.folder_for(owner, "agents", agent_id)
    return a


@router.patch("/{agent_id}/folder")
async def move_agent_to_folder(
    agent_id: str, request: Request, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    agent = await _agents.get(agent_id)
    if not agent:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
    if await get_user_role(ctx.user) != "admin" and agent.get("owner_id") != ctx.group_id:
        raise APIError(403, "forbidden", "Solo el propietario puede mover el agente")
    body = await request.json()
    try:
        await _folders.assign(
            ctx.group_id, "agents", agent_id,
            str(body["folder_id"]) if body.get("folder_id") else None,
        )
    except ValueError as exc:
        raise APIError(422, "folder_resource_mismatch", str(exc)) from exc
    return {**agent, "folder_id": body.get("folder_id")}


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        s = get_session(user)
        before = len(s.agents)
        s.agents = [a for a in s.agents if a.get("id") != agent_id]
        if len(s.agents) == before:
            raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
        return {"ok": True}
    a = await _agents.get(agent_id)
    if a and await get_user_role(user) != "admin" and a.get("owner_id") != group_id:
        raise APIError(403, "forbidden", "No tienes permiso para eliminar este agente")
    try:
        if not await _agents.delete(agent_id):
            raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
    except ValueError as e:
        raise APIError(403, "agent_delete_forbidden", str(e))
    if a:
        await _folders.remove_resource(
            str(a.get("owner_id") or group_id), "agents", agent_id
        )
    return {"ok": True}


@router.get("/{agent_id}/preferences")
async def get_agent_preferences(
    agent_id: str,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Return the saved connection preference for this user/agent pair."""
    async with open_db() as conn:
        row = await conn.fetchone(
            f"SELECT connection_id FROM user_agent_preferences "
            f"WHERE username={PH} AND agent_id={PH}",
            (user, agent_id),
        )
    return {"connection_id": row["connection_id"] if row else None}


@router.put("/{agent_id}/preferences")
async def put_agent_preferences(
    agent_id: str,
    body: _AgentPreferenceBody,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Upsert a connection preference for this user/agent pair."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                f"INSERT INTO user_agent_preferences (username, agent_id, connection_id, updated_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}) "
                f"ON CONFLICT (username, agent_id) DO UPDATE SET "
                f"connection_id=EXCLUDED.connection_id, updated_at=EXCLUDED.updated_at",
                (user, agent_id, body.connection_id, now_str),
            )
        else:
            await conn.execute(
                f"INSERT OR REPLACE INTO user_agent_preferences "
                f"(username, agent_id, connection_id, updated_at) VALUES ({PH}, {PH}, {PH}, {PH})",
                (user, agent_id, body.connection_id, now_str),
            )
        await conn.commit()
    return {"ok": True}


@router.get("/{agent_id}/export/{fmt}")
async def export_agent(
    agent_id: str, fmt: str, ctx: GroupContext = Depends(require_group)
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
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
    if not is_guest(user):
        await _assert_can_read_agent(agent_id, a, ctx)
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
        raise APIError(
            400,
            "export_format_unsupported",
            f"Formato '{fmt}' no soportado para tipo '{agent_obj.agent_type}'",
            extra={"format": fmt, "agent_type": agent_obj.agent_type},
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
            if mem_content:
                zf.writestr(".claude/CLAUDE.md", mem_content)
            _add_knowledge(zf, prefix=".claude/knowledge/")
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
                skill_md = _skill_md(sk)
                zf.writestr(f".github/skills/{sk_slug}/SKILL.md", skill_md)
            if mem_content:
                zf.writestr(".github/COPILOT_INSTRUCTIONS.md", mem_content)
            _add_knowledge(zf, prefix=".github/knowledge/")
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
    ctx: GroupContext = Depends(require_group),
    _rl: None = Depends(_chat_limiter),
) -> StreamingResponse:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        s = get_session(user)
        a = next(
            (a for a in s.agents if a.get("id") == agent_id), None
        ) or await _agents.get(agent_id, scope="public")
    else:
        a = await _agents.get(agent_id)
    if not a:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
    role = await get_user_role(user)
    if not is_guest(user):
        await _assert_can_read_agent(agent_id, a, ctx)
    a = _apply_locale(a, get_locale())

    body = await request.json()
    history: List[Dict[str, Any]] = body.get("messages") or []
    conversation_id: str = str(body.get("conversation_id") or "").strip()

    # Ollama virtual connections: "base_id::model_name"
    raw_conn_id = a.get("connection_id") or ""

    # Preferencia por usuario/agente: también debe aplicarse al propietario.
    # La extensión usa esto para cambiar de modelo sin modificar el agente ni
    # la conexión predeterminada para los demás usuarios.
    if not is_guest(user):
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
        not is_guest(user)
        and group_id != user
        and role != "admin"
        and base_conn_id
        and not await _groups.has_resource_permission(
            group_id, user, "connections", base_conn_id, "via_agent"
        )
    ):
        raise APIError(
            403,
            "forbidden",
            "No tienes permiso para usar esta conexión mediante agentes",
        )

    if not is_guest(user) and group_id != user and role != "admin":
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

    if is_guest(user):
        s = get_session(user)
        conn = next((c for c in s.connections if c.get("id") == base_conn_id), None)
        memory_store = GuestMemoryAdapter(s)
        knowledge_store = GuestKnowledgeAdapter(s)
    else:
        conn_id = base_conn_id
        if role == "admin":
            conn = await _conns.get(conn_id, None)
        else:
            from app.api.routes.connections import _get_conn_any

            conn = await _get_conn_any(conn_id, user, group_id)
        memory_store = _memory
        knowledge_store = _knowledge

    if conn and ollama_model:
        conn = {**conn, "model": ollama_model}

    if not conn:
        raise APIError(
            422, "agent_no_connection", "El agente no tiene conexión configurada"
        )

    from starlette.background import BackgroundTask

    done_event: List[dict] = []

    history_user_id = None if is_guest(user) else user

    async def _gen():
        async for chunk in stream_chat(
            a,
            conn,
            history,
            _skills,
            memory_store,
            knowledge_store,
            _chat,
            history_user_id,
            conversation_id or None,
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

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(_on_done),
    )
