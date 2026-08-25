"""Rutas de agentes: CRUD, exportación y chat SSE."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Response

from app.api.routes.auth import (
    GroupContext,
    require_group_session,
)
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, MEMORY_DIR, SKILLS_DIR
from app.errors import APIError
from app.middleware.locale import get_locale
from app.models.llm_orchestration import orchestration_id_from_connection
from app.models.request_bodies import AgentPayload
from app.pagination.models import OffsetParams
from app.services.agent_access import agent_access
from app.services.agent_listing import list_authenticated_agents
from app.services.agent_presentation import apply_agent_locale
from app.services.agent_presentation import validate_agent_scope as _check_scope
from app.services.publishing import assert_can_publish
from app.services.tool_access import resolve_accessible_tools
from app.sql import sql
from app.storage.agent_storage import AgentStorage
from app.storage.chat import ChatStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.db import open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.skill_storage import (
    ORIGIN_LABELS,
    SkillStorage,
    ensure_origin_label,
)
from app.storage.tool_storage import ToolStorage
from app.utils import flog
from app.utils.origin import assert_resource_writable, compute_origin_type

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
_knowledge_packs = KnowledgePackStorage()
_versions = ResourceVersionStorage()


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
        accessible = await _shares.is_accessible(
            _groups,
            resource_type="knowledge",
            resource_id=kid,
            owner_id=item.get("owner_id"),
            requester=user,
            requester_group=group_id,
        )
        pack_id = item.get("pack_id")
        if not accessible and pack_id:
            pack = await _knowledge_packs.get(pack_id, include_items=False)
            if pack:
                accessible = await _shares.is_accessible(
                    _groups,
                    resource_type="knowledge_pack",
                    resource_id=pack_id,
                    owner_id=pack.get("owner_id"),
                    requester=user,
                    requester_group=group_id,
                )
        if not accessible:
            raise APIError(
                403,
                "forbidden",
                "No tienes acceso a uno de los elementos de conocimiento indicados",
                extra={"resource": "knowledge", "id": kid},
            )
    for pack_id in payload.get("knowledge_packs") or []:
        pack = await _knowledge_packs.get(pack_id, include_items=False)
        if not pack:
            continue
        if not await _shares.is_accessible(
            _groups,
            resource_type="knowledge_pack",
            resource_id=pack_id,
            owner_id=pack.get("owner_id"),
            requester=user,
            requester_group=group_id,
        ):
            raise APIError(
                403,
                "forbidden",
                "No tienes acceso a uno de los packs de conocimiento indicados",
                extra={"resource": "knowledge_pack", "id": pack_id},
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


async def _validate_tool_refs(
    payload: Dict[str, Any], user: str, group_id: str, *, is_admin: bool
) -> None:
    """Tools are executable dependencies, so even admins cannot save ghosts."""
    try:
        await resolve_accessible_tools(
            payload.get("tools") or [],
            user_id=user,
            group_id=group_id,
            is_admin=is_admin,
            storage=_tools,
            shares=_shares,
            groups=_groups,
        )
    except APIError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "not_found" or (
            detail.get("code") == "invalid_field"
            and detail.get("field") == "implementation"
        ):
            raise APIError(
                422,
                "invalid_field",
                "Una de las Tools indicadas no tiene una implementación disponible",
                extra={
                    "resource": "tool",
                    "field": "tools",
                    "id": detail.get("resource_id"),
                },
            ) from exc
        raise


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
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    _check_scope(scope)
    locale = get_locale()
    return await list_authenticated_agents(
        _agents,
        ctx=ctx,
        scope=scope,
        label=label,
        include_inactive=include_inactive,
        page=OffsetParams(limit=limit, offset=offset),
        response=response,
        requested_group_id=group_id,
        present=lambda item: _apply_locale(item, locale),
    )


@router.post("")
async def save_agent(
    body: AgentPayload, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    payload = body.payload()
    requested_public_dependencies = payload.pop("publish_dependencies", None)
    connection_id = str(payload.get("connection_id") or "").strip()
    if connection_id and orchestration_id_from_connection(connection_id):
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
    if scope == "public":
        assert_can_publish(user)
    role = await get_user_role(user)
    labels = [str(label) for label in (payload.get("labels") or [scope]) if label]
    invalid = (
        [] if role == "admin" else [label for label in labels if label == "official"]
    )
    if invalid:
        message = (
            "El origen del recurso solo puede definirlo un administrador"
            if any(label in ORIGIN_LABELS for label in invalid)
            else "El agente contiene labels fuera del catálogo del sistema"
        )
        raise APIError(
            422,
            "invalid_field",
            message,
            extra={"field": "labels", "invalid": invalid},
        )
    payload["labels"] = ensure_origin_label(
        labels, None if role == "admin" else "community"
    )
    # Restrict editing to owner: if payload has an existing ID owned by someone else, block it
    agent_id_in_payload = payload.get("id")
    existing = None
    if agent_id_in_payload:
        existing = await _agents.get(agent_id_in_payload)
        if existing:
            assert_resource_writable(existing, "agent")
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
    if scope == "public":
        if requested_public_dependencies is not None:
            public_dependencies: List[str] | None = list(
                dict.fromkeys(
                    str(value) for value in requested_public_dependencies if value
                )
            )
        elif existing:
            # Clientes antiguos que editan un agente ya publicado no deben
            # cambiar involuntariamente su selección.
            existing_selection = existing.get("public_dependencies")
            public_dependencies = (
                [str(value) for value in existing_selection if value]
                if existing_selection is not None
                else None
            )
        else:
            # Un agente nuevo no publica dependencias por omisión.
            public_dependencies = []

        from app.services.publication_cascade import _agent_public_dependency_keys

        invalid_dependencies = sorted(
            set(public_dependencies or []) - _agent_public_dependency_keys(payload)
        )
        if invalid_dependencies:
            raise APIError(
                422,
                "invalid_field",
                "Hay dependencias seleccionadas que no pertenecen al agente",
                extra={
                    "field": "publish_dependencies",
                    "invalid": invalid_dependencies,
                },
            )
        payload["public_dependencies"] = public_dependencies
    else:
        payload["public_dependencies"] = []
    await _validate_tool_refs(payload, user, group_id, is_admin=role == "admin")
    if scope == "public":
        from app.services.tool_access import assert_tools_distributable_by_ids

        selected_tool_ids = [
            str(tool_id)
            for tool_id in (payload.get("tools") or [])
            if public_dependencies is None
            or f"tool:{tool_id}" in set(public_dependencies)
        ]
        await assert_tools_distributable_by_ids(
            selected_tool_ids,
            storage=_tools,
        )
    if role != "admin":
        await _validate_resource_refs(payload, user, group_id)
    try:
        # La etiqueta/``scope`` público debe materializar también la entrada de
        # catálogo. Antes el formulario guardaba el agente como público, pero
        # solo el endpoint de visibilidad escribía ``resource_social``; por eso
        # un agente creado directamente como público no aparecía en Explore.
        # Recurso, versión y catálogo comparten transacción para que nunca
        # queden estados públicos a medias.
        from app.services.publication_cascade import _cascade_publish_agent
        from app.services.social_catalog import (
            _assert_not_linked_copy,
            _upsert_social,
        )

        async with open_db() as conn:
            saved = await _agents.save(payload, scope, owner_id=group_id, conn=conn)
            await _versions.create(
                "agent",
                saved["id"],
                group_id,
                saved,
                user,
                reason="save",
                conn=conn,
            )
            if scope == "public":
                await _assert_not_linked_copy(conn, "agent", saved["id"], group_id)
                previous = await conn.fetchone(
                    sql("queries/agents:social_category_of_agent"),
                    ("agent", saved["id"], group_id),
                )
                await _upsert_social(
                    conn,
                    "agent",
                    saved["id"],
                    group_id,
                    saved.get("name", saved["id"]),
                    saved.get("description", ""),
                    previous["category"] if previous else "Other",
                    previous["trial_missing_deps"] if previous else "warn",
                    json.dumps(saved.get("tags") or []),
                    1,
                    json.dumps(saved.get("labels") or ["public"]),
                )
            else:
                await conn.execute(
                    sql("queries/agents:delete_social_of_agent"),
                    ("agent", saved["id"], group_id),
                )
            await conn.commit()
        if scope == "public":
            saved_selection = saved.get("public_dependencies")
            await _cascade_publish_agent(
                saved,
                group_id,
                group_id,
                selected=(
                    set(str(value) for value in saved_selection if value)
                    if saved_selection is not None
                    else None
                ),
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
    a = await _agents.get(agent_id)
    if a:
        assert_resource_writable(a, "agent")
    role = await get_user_role(user)
    if a and role != "admin" and a.get("owner_id") != group_id:
        raise APIError(403, "forbidden", "No tienes permiso para eliminar este agente")
    try:
        deleted = (
            await _agents.delete_as_admin(agent_id)
            if role == "admin"
            else await _agents.delete(agent_id, owner_id=group_id)
        )
        if not deleted:
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
    a = await _agents.get(agent_id)
    if not a:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    assert_resource_writable(a, "agent")
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
    agent_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_agent_active(agent_id, True, ctx)


@router.post("/{agent_id}/deactivate")
async def deactivate_agent(
    agent_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_agent_active(agent_id, False, ctx)
