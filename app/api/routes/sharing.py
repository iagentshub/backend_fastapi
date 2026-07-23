"""Rutas de comparticion de recursos con un workspace — /api/sharing.

No mueve ni copia el recurso: solo concede acceso de uso a TODO el workspace
destino (el dueño no cambia). Pensado sobre todo para conexiones (credenciales),
donde duplicar el secreto sería un riesgo de seguridad.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request

import app.config.data as _cfg
from app.api.routes.auth import WorkspaceContext, require_workspace
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.errors import APIError
from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import AgentStorage, SkillStorage
from app.storage.workspace_shares import WorkspaceShareStorage
from app.storage.workspaces import WorkspaceStorage

router = APIRouter(prefix="/api/sharing", tags=["sharing"])

_shares = WorkspaceShareStorage(DB_FILE)
_ws = WorkspaceStorage(DB_FILE)

_VALID_TYPES = {"agent", "connection", "knowledge", "skill"}


def _assert_valid_type(resource_type: str) -> None:
    if resource_type not in _VALID_TYPES:
        raise APIError(
            422,
            "invalid_resource_type",
            f"Tipo no valido: {resource_type}",
            extra={"type": resource_type},
        )


async def _resource_owner(resource_type: str, resource_id: str) -> Optional[str]:
    """Resuelve el owner_id actual de un recurso, sea cual sea su tipo."""
    if resource_type == "agent":
        item = await AgentStorage(_cfg.AGENTS_DIR).get(resource_id)
        return item.get("owner_id") if item else None
    if resource_type == "skill":
        item = await SkillStorage(_cfg.SKILLS_DIR).get_any(resource_id)
        return item.get("owner_id") if item else None
    if resource_type == "knowledge":
        item = await KnowledgeStorage(_cfg.DB_FILE).get(resource_id)
        return item.get("owner_id") if item else None
    # connection — ConnectionStorage.get() no incluye owner_id en el dict devuelto,
    # así que se resuelve con una consulta directa a la tabla.
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT owner_id FROM connections WHERE id = ?", (resource_id,)
        )
    return row[0] if row else None


async def _assert_can_share_resource(resource_type: str, resource_id: str, ctx: WorkspaceContext) -> None:
    """Solo el dueño directo del recurso, quien administra el workspace dueño, o un admin puede compartirlo."""
    role = await get_user_role(ctx.user)
    if role == "admin":
        return  # los admins pueden compartir cualquier recurso
    owner = await _resource_owner(resource_type, resource_id)
    if owner is None:
        raise APIError(404, "not_found", "Recurso no encontrado", extra={"resource": "resource"})
    if owner == ctx.user:
        return
    if await _ws.can_manage(owner, ctx.user):
        return
    raise APIError(403, "forbidden", "No tienes permisos sobre este recurso")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/{resource_type}/{resource_id}/groups")
async def list_resource_groups(
    resource_type: str,
    resource_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    """Lista los grupos (workspace IDs) que tienen acceso al recurso.

    Solo el dueño del recurso o un admin puede consultarlo.
    """
    _assert_valid_type(resource_type)
    role = await get_user_role(ctx.user)
    if role != "admin":
        owner = await _resource_owner(resource_type, resource_id)
        if owner is None:
            raise APIError(404, "not_found", "Recurso no encontrado", extra={"resource": "resource"})
        if owner != ctx.user and not await _ws.can_manage(owner, ctx.user):
            raise APIError(403, "forbidden", "No tienes permisos sobre este recurso")
    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT workspace_id FROM resource_workspace_shares "
            "WHERE resource_type = ? AND resource_id = ?",
            (resource_type, resource_id),
        )
    return {"group_ids": [row[0] for row in rows]}


async def _cascade_share_agent(
    agent_id: str,
    group_id: str,
    shared_by: str,
    shared_by_workspace: str = "",
) -> List[str]:
    """Al compartir un agente, comparte también sus skills y knowledge privados.

    Nunca comparte conexiones (credenciales) ni recursos ajenos al usuario que
    comparte. Devuelve la lista de IDs de recursos compartidos en cascada.
    """
    agent = await AgentStorage(_cfg.AGENTS_DIR).get(agent_id)
    if not agent:
        return []
    cascaded: List[str] = []
    skill_storage = SkillStorage(_cfg.SKILLS_DIR)
    knowledge_storage = KnowledgeStorage(_cfg.DB_FILE)

    # Identidades válidas del usuario que comparte (personal + workspace de equipo)
    _owner_ids = {shared_by, shared_by_workspace} - {""}

    for skill_id in (agent.get("skills") or []):
        skill = await skill_storage.get_any(skill_id)
        if not skill:
            continue
        # Solo skills privadas; las públicas ya son accesibles para todos
        if skill.get("scope", "private") != "private":
            continue
        # ALTO-5: no exponer skills ajenas — solo las del usuario que comparte
        if skill.get("owner_id") not in _owner_ids:
            continue
        await _shares.share_with_workspace("skill", skill_id, group_id, shared_by)
        cascaded.append(skill_id)

    for know_id in (agent.get("knowledge") or []):
        item = await knowledge_storage.get(know_id)
        if not item:
            continue
        # A1: no exponer knowledge ajeno — solo los items del usuario que comparte
        if item.get("owner_id") not in _owner_ids:
            continue
        await _shares.share_with_workspace("knowledge", know_id, group_id, shared_by)
        cascaded.append(know_id)

    return cascaded


@router.post("/{resource_type}/{resource_id}")
async def share_resource_with_workspace(
    resource_type: str,
    resource_id: str,
    request: Request,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    """Concede acceso de uso al grupo indicado.

    Para agentes: comparte en cascada sus skills y knowledge privados.
    Las conexiones NUNCA se comparten en cascada.
    """
    _assert_valid_type(resource_type)
    try:
        body = await request.json()
    except Exception:
        body = {}
    group_id = str(body.get("group_id") or "").strip()
    if not group_id:
        # Inferir del workspace activo si es un workspace de equipo (no personal)
        if ctx.workspace_id and ctx.workspace_id != ctx.user:
            group_id = ctx.workspace_id
    if not group_id:
        raise APIError(400, "missing_group_id", "group_id es obligatorio")
    role = await get_user_role(ctx.user)
    if role != "admin" and not await _ws.can_access(group_id, ctx.user):
        raise APIError(403, "forbidden", "No tienes acceso a este grupo")
    await _assert_can_share_resource(resource_type, resource_id, ctx)
    await _shares.share_with_workspace(resource_type, resource_id, group_id, ctx.user)

    # Cascade para agentes: compartir también skills y knowledge
    cascaded: List[str] = []
    if resource_type == "agent":
        cascaded = await _cascade_share_agent(resource_id, group_id, ctx.user, ctx.workspace_id)

    return {"ok": True, "cascaded": cascaded}


@router.delete("/{resource_type}/{resource_id}")
async def unshare_resource_from_workspace(
    resource_type: str,
    resource_id: str,
    request: Request,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    """Elimina el acceso de un grupo al recurso.

    Acepta group_id como query param (?group_id=uuid) o en el body JSON.
    """
    _assert_valid_type(resource_type)
    # Query param tiene prioridad; si no viene, leer del body
    group_id = request.query_params.get("group_id", "").strip()
    if not group_id:
        try:
            body = await request.json()
            group_id = str(body.get("group_id") or "").strip()
        except Exception:
            group_id = ""
    if not group_id:
        # Inferir del workspace activo si es un workspace de equipo (no personal)
        if ctx.workspace_id and ctx.workspace_id != ctx.user:
            group_id = ctx.workspace_id
    if not group_id:
        raise APIError(400, "missing_group_id", "group_id es obligatorio")

    role = await get_user_role(ctx.user)
    if role != "admin":
        owner = await _resource_owner(resource_type, resource_id)
        if owner is None:
            raise APIError(404, "not_found", "Recurso no encontrado", extra={"resource": "resource"})
        is_resource_owner = (owner == ctx.user)
        is_group_owner = await _ws.can_manage(group_id, ctx.user)
        if not is_resource_owner and not is_group_owner:
            raise APIError(
                403,
                "forbidden",
                "Solo el propietario del recurso o del grupo puede descompartirlo",
            )

    await _shares.unshare_from_workspace(resource_type, resource_id, group_id)
    return {"ok": True}
