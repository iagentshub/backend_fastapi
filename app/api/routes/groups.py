"""Rutas de grupos de workspace — /api/workspaces/{workspace_id}/groups."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.auth import WorkspaceContext, require_workspace
from app.auth.auth import get_user_role
from app.config.data import DB_FILE
from app.storage.db import run_db
from app.storage.groups import GroupStorage
from app.storage.workspaces import WorkspaceStorage

router = APIRouter(prefix="/api/workspaces", tags=["groups"])

_gs = GroupStorage(DB_FILE)
_ws = WorkspaceStorage(DB_FILE)


def _assert_can_manage(workspace_id: str, user: str) -> None:
    if not _ws.can_manage(workspace_id, user) and get_user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="Sin permisos para gestionar grupos")


def _assert_group_belongs(group_id: str, workspace_id: str) -> Dict[str, Any]:
    group = _gs.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    if group["workspace_id"] != workspace_id:
        raise HTTPException(status_code=404, detail="Grupo no encontrado en este workspace")
    return group


# ── CRUD de grupos ─────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/groups")
async def list_groups(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    def _sync():
        if not _ws.can_access(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            raise HTTPException(status_code=403, detail="Sin acceso a este workspace")
        return _gs.list_for_workspace(workspace_id)
    return await run_db(_sync)


@router.post("/{workspace_id}/groups")
async def create_group(
    workspace_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    if len(name) > 80:
        raise HTTPException(status_code=400, detail="El nombre no puede superar 80 caracteres")
    def _sync():
        _assert_can_manage(workspace_id, ctx.user)
        try:
            return _gs.create(workspace_id, name, ctx.user)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return await run_db(_sync)


@router.patch("/{workspace_id}/groups/{group_id}")
async def rename_group(
    workspace_id: str,
    group_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    def _sync():
        _assert_can_manage(workspace_id, ctx.user)
        _assert_group_belongs(group_id, workspace_id)
        _gs.rename(group_id, name)
    await run_db(_sync)
    return {"ok": True, "id": group_id, "name": name}


@router.delete("/{workspace_id}/groups/{group_id}")
async def delete_group(
    workspace_id: str,
    group_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    def _sync():
        _assert_can_manage(workspace_id, ctx.user)
        _assert_group_belongs(group_id, workspace_id)
        _gs.delete(group_id)
    await run_db(_sync)
    return {"ok": True}


# ── Miembros de grupo ──────────────────────────────────────────────────────────

@router.get("/{workspace_id}/groups/{group_id}/members")
async def list_group_members(
    workspace_id: str,
    group_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    def _sync():
        if not _ws.can_access(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            raise HTTPException(status_code=403, detail="Sin acceso")
        _assert_group_belongs(group_id, workspace_id)
        return _gs.list_members(group_id)
    return await run_db(_sync)


@router.post("/{workspace_id}/groups/{group_id}/members")
async def add_group_member(
    workspace_id: str,
    group_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    username = str(body.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="username es obligatorio")
    def _sync():
        _assert_can_manage(workspace_id, ctx.user)
        _assert_group_belongs(group_id, workspace_id)
        if not _ws.is_member(workspace_id, username):
            raise HTTPException(status_code=400, detail="El usuario no es miembro del workspace")
        _gs.add_member(group_id, workspace_id, username)
    await run_db(_sync)
    return {"ok": True, "group_id": group_id, "username": username}


@router.delete("/{workspace_id}/groups/{group_id}/members/{username}")
async def remove_group_member(
    workspace_id: str,
    group_id: str,
    username: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    def _sync():
        _assert_can_manage(workspace_id, ctx.user)
        _assert_group_belongs(group_id, workspace_id)
        if not _gs.remove_member(group_id, username):
            raise HTTPException(status_code=404, detail="Miembro no encontrado en el grupo")
    await run_db(_sync)
    return {"ok": True}
