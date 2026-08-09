"""Rutas de agentes: CRUD, exportación y chat SSE."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Response

from app.api.pagination import paginar
from app.api.routes.auth import (
    GroupContext,
    require_group,
    require_group_session,
)
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, MEMORY_DIR, SKILLS_DIR
from app.config.session import RATE_CHAT_CALLS, RATE_CHAT_WINDOW
from app.errors import APIError
from app.middleware.locale import get_locale
from app.middleware.ratelimit import RateLimiter
from app.models.llm_orchestration import orchestration_id_from_connection
from app.models.request_bodies import AgentPayload
from app.services.agent_access import agent_access
from app.services.agent_presentation import apply_agent_locale
from app.services.agent_presentation import validate_agent_scope as _check_scope
from app.storage.agent_storage import AgentStorage
from app.storage.chat import ChatStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.guest import (
    get_session,
    is_guest,
)
from app.storage.knowledge import KnowledgeStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.origin import compute_origin_type

router = APIRouter(prefix="/api/agents", tags=["agents"])

_agents = AgentStorage(AGENTS_DIR)
_conns = ConnectionStorage()
_skills = SkillStorage(SKILLS_DIR)
_prompts = PromptStorage()
_tools = ToolStorage()
_memory = MemoryStorage(MEMORY_DIR)
_shares = GroupShareStorage()
_groups = GroupStorage()
_chat = ChatStorage()
_knowledge = KnowledgeStorage()
_versions = ResourceVersionStorage()
_chat_limiter = RateLimiter(calls=RATE_CHAT_CALLS, window=RATE_CHAT_WINDOW)


async def _validate_resource_refs(
    payload: Dict[str, Any], user: str, group_id: str
) -> None:
    """Rechaza IDs de skills/knowledge/prompts que el usuario no puede
    legítimamente usar (ni son suyos, públicos, ni están compartidos con él).

    Sin esto, cualquiera puede adjuntar el ID de una skill, un knowledge o un
    prompt ajenos a su propio agente y leer su contenido completo vía chat o
    export (mismo problema que ALTO-5/A1 en sharing.py, sin corregir aquí).
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
    for pid in payload.get("prompts") or []:
        prompt = await _prompts.get_any(pid)
        if not prompt or prompt.get("scope") == "public":
            continue
        if not await _shares.is_accessible(
            _groups,
            resource_type="prompt",
            resource_id=pid,
            owner_id=prompt.get("owner_id"),
            requester=user,
            requester_group=group_id,
        ):
            raise APIError(
                403,
                "forbidden",
                "No tienes acceso a uno de los prompts indicados",
                extra={"resource": "prompt", "id": pid},
            )
    for tid in payload.get("tools") or []:
        tool = await _tools.get_any(tid)
        if not tool or tool.get("scope") == "public":
            continue
        if not await _shares.is_accessible(
            _groups,
            resource_type="tool",
            resource_id=tid,
            owner_id=tool.get("owner_id"),
            requester=user,
            requester_group=group_id,
        ):
            raise APIError(
                403,
                "forbidden",
                "No tienes acceso a una de las tools indicadas",
                extra={"resource": "tool", "id": tid},
            )


async def _assert_can_read_agent(
    agent_id: str,
    agent: Dict[str, Any],
    ctx: GroupContext,
) -> None:
    await agent_access.assert_can_read(agent_id, agent, ctx)


async def _conn_owner(user: str) -> str | None:
    return None if await get_user_role(user) == "admin" else user


def _apply_locale(agent: Dict[str, Any], locale: str) -> Dict[str, Any]:
    return apply_agent_locale(agent, locale, AGENTS_DIR)


@router.get("")
async def list_agents(
    scope: str = "all",
    label: Optional[str] = None,
    owner_scope: str = "group",
    group_id: Optional[str] = None,
    include_inactive: bool = False,
    limit: int = Query(0, ge=0, description="Máx. items. 0 = sin límite"),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    _check_scope(scope)
    locale = get_locale()
    user = ctx.user
    if is_guest(user):
        s = get_session(user)
        public = await _agents.list("public") if scope in ("public", "all") else []
        private = s.agents if scope in ("private", "all") else []
        items = public + private
        if not include_inactive:
            items = [agent for agent in items if agent.get("is_active", True)]
        if label:
            items = [a for a in items if label in (a.get("labels") or [])]
        items = paginar(items, limit, offset, response)
        result: List[Dict[str, Any]] = []
        for a in items:
            a = _apply_locale(a, locale)
            a["origin_type"] = compute_origin_type(a)
            result.append(a)
        return result
    role = await get_user_role(user)
    if group_id is not None:
        # Filtro por grupo: se aplica siempre, incluido admin
        if role != "admin" and not await _groups.can_access(group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        shared_ids = set(await _shares.get_group_shared_resource_ids(group_id, "agent"))
        agents = await _agents.list_visible(scope, resource_ids=list(shared_ids))
        for a in agents:
            a["_shared"] = True
            a["_group_id"] = group_id
    else:
        # Vista por defecto: propios + recursos del group activo + compartidos
        # (incluye admin: la visibilidad global de admin se sirve vía /api/admin/agents,
        # no filtrar aquí exponía agentes privados de otros usuarios sin marcar como ajenos)
        # En group de equipo (group_id != user), owner_id puede ser el UUID del group.
        group_id = ctx.group_id
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

        # Los tres orígenes de la vista se piden a la BD en una sola consulta,
        # en vez de traer la tabla entera y descartar en Python lo que no es de
        # este usuario.
        agents = await _agents.list_visible(
            scope,
            owner_ids=[user, group_id] if group_id != user else [user],
            resource_ids=list(shared_map),
        )
        own = [
            a
            for a in agents
            if a.get("owner_id") == user or a.get("owner_id") == group_id
        ]
        own_ids = {a["id"] for a in own}

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
    if not include_inactive:
        # Ocultar recursos desactivados salvo que se pidan explícitamente.
        # Los compartidos ajenos nunca se muestran inactivos.
        agents = [a for a in agents if a.get("is_active", True)]
    if ctx.group_id != user and role != "admin":
        # Una consulta a group_members para los N agentes, no una por agente:
        # la fila del miembro (y su JSON de permisos) es la misma para todos.
        permitido = await _groups.permission_checker(ctx.group_id, user)
        agents = [a for a in agents if permitido("agents", a["id"], "use")]
    agents = paginar(agents, limit, offset, response)
    enriched: List[Dict[str, Any]] = []
    for a in agents:
        a = _apply_locale(a, locale)
        a["origin_type"] = compute_origin_type(a)
        enriched.append(a)
    return enriched


@router.post("")
async def save_agent(
    body: AgentPayload, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    payload = body.payload()
    connection_id = str(payload.get("connection_id") or "").strip()
    if (
        connection_id
        and orchestration_id_from_connection(connection_id)
        and not is_guest(ctx.user)
    ):
        from app.services.connection_access import connection_access

        if not await connection_access.get_accessible(
            connection_id, ctx.user, ctx.group_id
        ):
            raise APIError(
                422,
                "invalid_field",
                "La orquestación LLM no está disponible",
                extra={"field": "connection_id"},
            )
    scope = str(payload.pop("scope", "private") or "private")
    if scope not in ("public", "private"):
        raise APIError(
            400, "invalid_field", "Scope no válido", extra={"field": "scope"}
        )
    if is_guest(user):
        s = get_session(user)
        guest_id = payload.get("id")
        if guest_id and not any(a.get("id") == guest_id for a in s.agents):
            guest_id = None
        agent: Dict[str, Any] = {
            **payload,
            "id": guest_id or generate_id(),
            "resource_type": "agent",
            "scope": "private",
            "is_active": True,
        }
        s.agents = [a for a in s.agents if a.get("id") != agent["id"]]
        s.agents.append(agent)
        return agent
    role = await get_user_role(user)
    # Restrict editing to owner: if payload has an existing ID owned by someone else, block it
    agent_id_in_payload = payload.get("id")
    existing = None
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
    if agent_id_in_payload and not existing:
        # Un id entrante solo es válido para editar una fila existente;
        # en altas el id lo genera siempre el servidor.
        payload.pop("id", None)
    if role != "admin":
        await _validate_resource_refs(payload, user, group_id)
    try:
        saved = await _agents.save(payload, scope, owner_id=group_id)
        await _versions.create(
            "agent", saved["id"], group_id, saved, user, reason="save"
        )
        action = "actualizado" if existing else "creado"
        flog.info(
            f"Agente {action}: {saved['id']} {saved.get('name', '')!r}",
            username=user,
        )
        return saved
    except ValueError as e:
        raise APIError(422, "agent_invalid_payload", str(e))


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user = ctx.user
    if is_guest(user):
        s = get_session(user)
        a = next(
            (a for a in s.agents if a.get("id") == agent_id), None
        ) or await _agents.get(agent_id, scope="public")
        if not a:
            raise APIError(
                404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
            )
        a = _apply_locale(a, get_locale())
        a["origin_type"] = compute_origin_type(a)
        return a
    a = await _agents.get(agent_id)
    if not a:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    await _assert_can_read_agent(agent_id, a, ctx)
    if not await _groups.has_resource_permission(
        ctx.group_id, user, "agents", agent_id, "use"
    ):
        raise APIError(403, "forbidden", "Sin permiso para usar este agente")
    a = _apply_locale(a, get_locale())
    a["origin_type"] = compute_origin_type(a)
    return a


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        s = get_session(user)
        before = len(s.agents)
        s.agents = [a for a in s.agents if a.get("id") != agent_id]
        if len(s.agents) == before:
            raise APIError(
                404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
            )
        return {"ok": True}
    a = await _agents.get(agent_id)
    role = await get_user_role(user)
    if a and role != "admin" and a.get("owner_id") != group_id:
        raise APIError(403, "forbidden", "No tienes permiso para eliminar este agente")
    try:
        delete_owner = None if role == "admin" else group_id
        if not await _agents.delete(agent_id, owner_id=delete_owner):
            raise APIError(
                404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
            )
    except ValueError as e:
        raise APIError(403, "agent_delete_forbidden", str(e))
    flog.info(
        f"Agente borrado: {agent_id} {(a or {}).get('name', '')!r}", username=user
    )
    return {"ok": True}


async def _set_agent_active(
    agent_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        raise APIError(403, "forbidden", "Los invitados no pueden desactivar agentes")
    a = await _agents.get(agent_id)
    if not a:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    role = await get_user_role(user)
    if role != "admin" and a.get("owner_id") != group_id:
        raise APIError(403, "forbidden", "Solo el propietario puede cambiar el estado")
    owner = None if role == "admin" else group_id
    if not await _agents.set_active(agent_id, owner, active):
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    estado = "activado" if active else "desactivado"
    flog.info(f"Agente {estado}: {agent_id} {a.get('name', '')!r}", username=user)
    return {"ok": True, "is_active": active}


@router.post("/{agent_id}/activate")
async def activate_agent(
    agent_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_agent_active(agent_id, True, ctx)


@router.post("/{agent_id}/deactivate")
async def deactivate_agent(
    agent_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_agent_active(agent_id, False, ctx)
