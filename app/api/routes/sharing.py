"""Rutas de comparticion de recursos con grupos de workspace — /api/sharing."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.auth import WorkspaceContext, require_workspace
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.storage.groups import GroupStorage
from app.storage.workspaces import WorkspaceStorage

router = APIRouter(prefix="/api/sharing", tags=["sharing"])

_gs = GroupStorage(DB_FILE)
_ws = WorkspaceStorage(DB_FILE)

_VALID_TYPES = {"agent", "connection", "knowledge", "skill"}


def _assert_valid_type(resource_type: str) -> None:
    if resource_type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Tipo no valido: {resource_type}")


def _assert_group_in_workspace(group_id: str, workspace_id: str) -> Dict[str, Any]:
    group = _gs.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    if group["workspace_id"] != workspace_id:
        raise HTTPException(status_code=403, detail="El grupo no pertenece a este workspace")
    return group


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/{resource_type}/{resource_id}")
async def get_resource_sharing(
    resource_type: str,
    resource_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    """Grupos con los que esta compartido un recurso."""
    _assert_valid_type(resource_type)
    return _gs.get_resource_groups(resource_type, resource_id)


@router.post("/{resource_type}/{resource_id}")
async def share_resource(
    resource_type: str,
    resource_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    """Compartir un recurso con un grupo del workspace actual."""
    _assert_valid_type(resource_type)
    group_id = str(body.get("group_id") or "").strip()
    if not group_id:
        raise HTTPException(status_code=422, detail="group_id requerido")
    _assert_group_in_workspace(group_id, ctx.workspace_id)
    if not _ws.can_manage(ctx.workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Solo los admins del workspace pueden compartir recursos")
    _gs.share_resource(resource_type, resource_id, group_id, ctx.user)
    return {"ok": True}


@router.delete("/{resource_type}/{resource_id}/{group_id}")
async def unshare_resource(
    resource_type: str,
    resource_id: str,
    group_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    """Dejar de compartir un recurso con un grupo."""
    _assert_valid_type(resource_type)
    if not _ws.can_manage(ctx.workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos")
    _gs.unshare_resource(resource_type, resource_id, group_id)
    return {"ok": True}


@router.get("/by-group/{group_id}/{resource_type}")
async def get_group_resources(
    group_id: str,
    resource_type: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    """Recursos compartidos con un grupo (para el directorio)."""
    _assert_valid_type(resource_type)
    group = _gs.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    if not _gs.is_member(group_id, ctx.user) and not _ws.can_manage(group["workspace_id"], ctx.user) and get_user_role(ctx.user) != "admin":
        raise HTTPException(status_code=403, detail="Sin acceso a este grupo")
    return _gs.get_group_resources(group_id, resource_type)
