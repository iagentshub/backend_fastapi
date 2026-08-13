"""Rutas de comparticion de recursos con un group — /api/sharing.

No mueve ni copia el recurso: solo concede acceso de uso a TODO el group
destino (el dueño no cambia). Pensado sobre todo para conexiones (credenciales),
donde duplicar el secreto sería un riesgo de seguridad.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request

import app.config.data as _cfg
from app.api.routes.auth import GroupContext, require_group
from app.auth.auth import get_user_role
from app.errors import APIError
from app.models.request_bodies import GroupShareBody
from app.models.resource_types import RESOURCE_TYPES
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.db import open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.llm_orchestrations import LLMOrchestrationStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage
from app.utils.origin import is_linked_resource

router = APIRouter(prefix="/api/sharing", tags=["sharing"])

_shares = GroupShareStorage()
_groups = GroupStorage()

# Singletons de módulo: construir un storage dentro de cada handler reejecutaba
# su migración legacy (el flag era de instancia), y con ella un SELECT COUNT(*)
# por petición. Mismo patrón que agents.py y connections.py.
_agents_store = AgentStorage(_cfg.AGENTS_DIR)
_connections_store = ConnectionStorage()
_skills_store = SkillStorage(_cfg.SKILLS_DIR)
_prompts_store = PromptStorage()
_tools_store = ToolStorage()
_knowledge_store = KnowledgeStorage()
_workflows_store = WorkflowStorage()
_orch_store = LLMOrchestrationStorage()

_VALID_TYPES = RESOURCE_TYPES


def _assert_valid_type(resource_type: str) -> None:
    if resource_type not in _VALID_TYPES:
        raise APIError(
            422,
            "invalid_resource_type",
            f"Tipo no valido: {resource_type}",
            extra={"type": resource_type},
        )


async def _resource_record(
    resource_type: str, resource_id: str
) -> Optional[Dict[str, Any]]:
    """Resuelve el recurso persistido, incluyendo etiquetas de propiedad."""
    if resource_type == "agent":
        return await _agents_store.get(resource_id)
    if resource_type == "skill":
        return await _skills_store.get_any(resource_id)
    if resource_type == "knowledge":
        return await _knowledge_store.get(resource_id)
    if resource_type == "workflow":
        return await _workflows_store.get_any(resource_id)
    if resource_type == "llm_orchestration":
        return await _orch_store.get_any(resource_id)
    if resource_type == "prompt":
        return await _prompts_store.get_any(resource_id)
    if resource_type == "tool":
        return await _tools_store.get_any(resource_id)
    return await _connections_store.get(resource_id)


async def _resource_owner(resource_type: str, resource_id: str) -> Optional[str]:
    item = await _resource_record(resource_type, resource_id)
    return str(item.get("owner_id")) if item and item.get("owner_id") else None


async def _assert_can_share_resource(
    resource_type: str, resource_id: str, ctx: GroupContext
) -> None:
    """Solo el dueño directo del recurso, quien administra el group dueño, o un admin puede compartirlo."""
    item = await _resource_record(resource_type, resource_id)
    if item is None:
        raise APIError(
            404, "not_found", "Recurso no encontrado", extra={"resource": "resource"}
        )
    if is_linked_resource(item):
        raise APIError(
            403,
            "linked_resource_read_only",
            "Los enlaces son de solo lectura y no se pueden compartir",
            extra={"resource": resource_type},
        )
    role = await get_user_role(ctx.user)
    if role == "admin":
        return  # los admins pueden compartir cualquier recurso gestionable
    owner = str(item.get("owner_id") or "")
    if owner == ctx.user:
        return
    if await _groups.can_manage(owner, ctx.user):
        return
    raise APIError(403, "forbidden", "No tienes permisos sobre este recurso")


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/{resource_type}/{resource_id}/groups")
async def list_resource_groups(
    resource_type: str,
    resource_id: str,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    """Lista los grupos (group IDs) que tienen acceso al recurso.

    Solo el dueño del recurso o un admin puede consultarlo.
    """
    _assert_valid_type(resource_type)
    role = await get_user_role(ctx.user)
    if role != "admin":
        owner = await _resource_owner(resource_type, resource_id)
        if owner is None:
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado",
                extra={"resource": "resource"},
            )
        if owner != ctx.user and not await _groups.can_manage(owner, ctx.user):
            raise APIError(403, "forbidden", "No tienes permisos sobre este recurso")
    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT group_id FROM resource_group_shares "
            "WHERE resource_type = ? AND resource_id = ?",
            (resource_type, resource_id),
        )
    return {"group_ids": [row[0] for row in rows]}


async def _cascade_share_agent(
    agent_id: str,
    group_id: str,
    shared_by: str,
    shared_by_group: str = "",
) -> List[str]:
    """Al compartir un agente, comparte también sus skills, knowledge y prompts
    privados.

    Nunca comparte conexiones (credenciales) ni recursos ajenos al usuario que
    comparte. Devuelve la lista de IDs de recursos compartidos en cascada.
    """
    agent = await _agents_store.get(agent_id)
    if not agent:
        return []
    cascaded: List[str] = []
    skill_storage = _skills_store
    knowledge_storage = _knowledge_store
    prompt_storage = _prompts_store

    # Identidades válidas del usuario que comparte (personal + group de equipo)
    _owner_ids = {shared_by, shared_by_group} - {""}

    for skill_id in agent.get("skills") or []:
        skill = await skill_storage.get_any(skill_id)
        if not skill:
            continue
        # Solo skills privadas; las públicas ya son accesibles para todos
        if skill.get("scope", "private") != "private":
            continue
        # ALTO-5: no exponer skills ajenas — solo las del usuario que comparte
        if skill.get("owner_id") not in _owner_ids:
            continue
        await _shares.share_with_group("skill", skill_id, group_id, shared_by)
        cascaded.append(skill_id)

    for know_id in agent.get("knowledge") or []:
        item = await knowledge_storage.get(know_id)
        if not item:
            continue
        # A1: no exponer knowledge ajeno — solo los items del usuario que comparte
        if item.get("owner_id") not in _owner_ids:
            continue
        await _shares.share_with_group("knowledge", know_id, group_id, shared_by)
        cascaded.append(know_id)

    for prompt_id in agent.get("prompts") or []:
        prompt = await prompt_storage.get_any(prompt_id)
        if not prompt:
            continue
        # Solo prompts privados; los públicos ya son accesibles para todos
        if prompt.get("scope", "private") != "private":
            continue
        # No exponer prompts ajenos — solo los del usuario que comparte
        if prompt.get("owner_id") not in _owner_ids:
            continue
        await _shares.share_with_group("prompt", prompt_id, group_id, shared_by)
        cascaded.append(prompt_id)

    return cascaded


async def _cascade_share_workflow(
    workflow_id: str,
    group_id: str,
    shared_by: str,
    shared_by_group: str,
) -> List[str]:
    """Comparte los agentes propios usados por una orquestación y sus dependencias."""
    workflow = await _workflows_store.get_any(workflow_id)
    if not workflow:
        return []
    allowed_owners = {
        str(workflow.get("owner_id") or ""),
        shared_by,
        shared_by_group,
    } - {""}
    cascaded: List[str] = []
    for node in workflow.get("definition", {}).get("nodes", []):
        agent_id = str(node.get("agent_id") or "")
        if not agent_id or agent_id in cascaded:
            continue
        agent = await _agents_store.get(agent_id)
        if not agent or str(agent.get("owner_id") or "") not in allowed_owners:
            continue
        await _shares.share_with_group("agent", agent_id, group_id, shared_by)
        cascaded.append(agent_id)
        cascaded.extend(
            await _cascade_share_agent(agent_id, group_id, shared_by, shared_by_group)
        )
    return cascaded


@router.post("/{resource_type}/{resource_id}")
async def share_resource_with_group(
    resource_type: str,
    resource_id: str,
    body: GroupShareBody | None = None,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    """Concede acceso de uso al grupo indicado.

    Para agentes comparte sus skills y knowledge privados. Una orquestación
    LLM comparte solo su definición; cada miembro vincula conexiones propias o
    compartidas con él, sin propagar las credenciales del autor.
    """
    _assert_valid_type(resource_type)
    payload = body.payload() if body else {}
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        # Inferir del group activo si es un group de equipo (no personal)
        if ctx.group_id and ctx.group_id != ctx.user:
            group_id = ctx.group_id
    if not group_id:
        raise APIError(400, "missing_group_id", "group_id es obligatorio")
    role = await get_user_role(ctx.user)
    if role != "admin" and not await _groups.can_access(group_id, ctx.user):
        raise APIError(403, "forbidden", "No tienes acceso a este grupo")
    await _assert_can_share_resource(resource_type, resource_id, ctx)
    await _shares.share_with_group(resource_type, resource_id, group_id, ctx.user)

    # Cascade para agentes: compartir también skills y knowledge
    cascaded: List[str] = []
    if resource_type == "agent":
        cascaded = await _cascade_share_agent(
            resource_id, group_id, ctx.user, ctx.group_id
        )
    elif resource_type == "workflow":
        cascaded = await _cascade_share_workflow(
            resource_id, group_id, ctx.user, ctx.group_id
        )

    return {"ok": True, "cascaded": cascaded}


@router.delete("/{resource_type}/{resource_id}")
async def unshare_resource_from_group(
    resource_type: str,
    resource_id: str,
    request: Request,
    body: GroupShareBody | None = None,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    """Elimina el acceso de un grupo al recurso.

    Acepta group_id como query param (?group_id=uuid) o en el body JSON.
    """
    _assert_valid_type(resource_type)
    # Query param tiene prioridad; si no viene, leer del body
    group_id = request.query_params.get("group_id", "").strip()
    if not group_id:
        payload = body.payload() if body else {}
        group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        # Inferir del group activo si es un group de equipo (no personal)
        if ctx.group_id and ctx.group_id != ctx.user:
            group_id = ctx.group_id
    if not group_id:
        raise APIError(400, "missing_group_id", "group_id es obligatorio")

    role = await get_user_role(ctx.user)
    if role != "admin":
        owner = await _resource_owner(resource_type, resource_id)
        if owner is None:
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado",
                extra={"resource": "resource"},
            )
        is_resource_owner = owner == ctx.user
        is_group_owner = await _groups.can_manage(group_id, ctx.user)
        if not is_resource_owner and not is_group_owner:
            raise APIError(
                403,
                "forbidden",
                "Solo el propietario del recurso o del grupo puede descompartirlo",
            )

    await _shares.unshare_from_group(resource_type, resource_id, group_id)
    return {"ok": True}
