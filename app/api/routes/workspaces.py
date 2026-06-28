"""Rutas de workspaces — CRUD y cambio de workspace activo."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.routes.auth import WorkspaceContext, require_workspace
from app.auth.auth import create_token, get_user_role
from app.api.routes.auth import require_auth
from app.config.data import DB_FILE
from app.config.session import SECURE_COOKIES
from app.storage.db import run_db
from app.storage.guest import is_guest
from app.storage.workspaces import WorkspaceStorage

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])

_ws = WorkspaceStorage(DB_FILE)


def _assert_not_guest(user: str) -> None:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden gestionar workspaces")


# ── Listar workspaces del usuario ──────────────────────────────────────────────

@router.get("")
async def list_workspaces(ctx: WorkspaceContext = Depends(require_workspace)) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    team_workspaces = await run_db(lambda: _ws.list_for_user(ctx.user))
    return [
        {
            "id": ctx.user,
            "name": "Personal",
            "type": "personal",
            "role": "owner",
            "active": ctx.workspace_id == ctx.user,
        },
        *[
            {**ws, "type": "team", "active": ctx.workspace_id == ws["id"]}
            for ws in team_workspaces
        ],
    ]


# ── Crear workspace de equipo ──────────────────────────────────────────────────

@router.post("")
async def create_workspace(
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    if len(name) > 80:
        raise HTTPException(status_code=400, detail="El nombre no puede superar los 80 caracteres")
    ws = await run_db(lambda: _ws.create(name, created_by=ctx.user))
    return {**ws, "type": "team", "active": False}


# ── Renombrar workspace ────────────────────────────────────────────────────────

@router.patch("/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if workspace_id == ctx.user:
        raise HTTPException(status_code=400, detail="El workspace personal no se puede renombrar aquí")
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    def _sync():
        if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            return False, False
        return True, _ws.update(workspace_id, name)

    allowed, updated = await run_db(_sync)
    if not allowed:
        raise HTTPException(status_code=403, detail="Sin permisos para modificar este workspace")
    if not updated:
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    return {"ok": True, "id": workspace_id, "name": name}


# ── Eliminar workspace ─────────────────────────────────────────────────────────

@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if workspace_id == ctx.user:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu workspace personal")

    def _sync():
        ws = _ws.get(workspace_id)
        if not ws:
            return "not_found", None
        if ws["created_by"] != ctx.user and get_user_role(ctx.user) != "admin":
            return "forbidden", None
        _ws.delete(workspace_id)
        return "ok", None

    result, _ = await run_db(_sync)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if result == "forbidden":
        raise HTTPException(status_code=403, detail="Solo el creador puede eliminar el workspace")
    return {"ok": True}


# ── Cambiar workspace activo ───────────────────────────────────────────────────

@router.post("/switch/{workspace_id}")
async def switch_workspace(
    workspace_id: str,
    response: Response,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    user = ctx.user

    def _sync():
        role = get_user_role(user)
        if workspace_id != user and not _ws.can_access(workspace_id, user) and role != "admin":
            return None
        return create_token(user, workspace_id=workspace_id)

    token = await run_db(_sync)
    if token is None:
        raise HTTPException(status_code=403, detail="No tienes acceso a este workspace")
    response.set_cookie(
        "ga_token", token, httponly=True, samesite="lax",
        secure=SECURE_COOKIES, max_age=43200,
    )
    return {"ok": True, "workspace_id": workspace_id}


# ── Miembros ───────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/members")
async def list_members(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)

    def _sync():
        if not _ws.can_access(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            return None
        return _ws.list_members(workspace_id)

    members = await run_db(_sync)
    if members is None:
        raise HTTPException(status_code=403, detail="Sin acceso a este workspace")
    return members


@router.post("/{workspace_id}/members")
async def add_member(
    workspace_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    username = str(body.get("username") or "").strip()
    role = str(body.get("role") or "member").strip()
    if not username:
        raise HTTPException(status_code=400, detail="El username es obligatorio")
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="Rol inválido")

    def _sync():
        from app.auth.auth import get_user_by_username
        if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            return "forbidden"
        if not get_user_by_username(username):
            return "no_user"
        if not _ws.get(workspace_id):
            return "no_ws"
        _ws.add_member(workspace_id, username, role)
        return "ok"

    result = await run_db(_sync)
    if result == "forbidden":
        raise HTTPException(status_code=403, detail="Sin permisos para invitar miembros")
    if result == "no_user":
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if result == "no_ws":
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    return {"ok": True, "workspace_id": workspace_id, "username": username, "role": role}


@router.delete("/{workspace_id}/members/{username}")
async def remove_member(
    workspace_id: str,
    username: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)

    def _sync():
        if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            if username != ctx.user:
                return "forbidden"
        ws = _ws.get(workspace_id)
        if not ws:
            return "no_ws"
        if username == ws["created_by"]:
            return "creator"
        if not _ws.remove_member(workspace_id, username):
            return "no_member"
        return "ok"

    result = await run_db(_sync)
    if result == "forbidden":
        raise HTTPException(status_code=403, detail="Sin permisos para eliminar miembros")
    if result == "no_ws":
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if result == "creator":
        raise HTTPException(status_code=400, detail="No puedes eliminar al creador del workspace")
    if result == "no_member":
        raise HTTPException(status_code=404, detail="Miembro no encontrado")
    return {"ok": True}


@router.patch("/{workspace_id}/members/{username}")
async def update_member_role(
    workspace_id: str,
    username: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    role = str(body.get("role") or "").strip()
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="Rol inválido")

    def _sync():
        if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            return "forbidden"
        if not _ws.update_member_role(workspace_id, username, role):
            return "not_found"
        return "ok"

    result = await run_db(_sync)
    if result == "forbidden":
        raise HTTPException(status_code=403, detail="Sin permisos para cambiar roles")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Miembro no encontrado")
    return {"ok": True, "workspace_id": workspace_id, "username": username, "role": role}


# ── Invitaciones ───────────────────────────────────────────────────────────────

@router.get("/my-invitations")
async def my_invitations(ctx: WorkspaceContext = Depends(require_workspace)) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)
    return await run_db(lambda: _ws.list_my_invitations(ctx.user))


@router.post("/invitations/{inv_id}/accept")
async def accept_invitation(
    inv_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    workspace_id = await run_db(lambda: _ws.accept_invitation(inv_id, ctx.user))
    if not workspace_id:
        raise HTTPException(status_code=404, detail="Invitacion no encontrada")
    return {"ok": True, "workspace_id": workspace_id}


@router.post("/invitations/{inv_id}/reject")
async def reject_invitation(
    inv_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    if not await run_db(lambda: _ws.reject_invitation(inv_id, ctx.user)):
        raise HTTPException(status_code=404, detail="Invitacion no encontrada")
    return {"ok": True}


@router.get("/{workspace_id}/invitations")
async def list_workspace_invitations(
    workspace_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> List[Dict[str, Any]]:
    _assert_not_guest(ctx.user)

    def _sync():
        if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            return None
        return _ws.list_invitations(workspace_id)

    invitations = await run_db(_sync)
    if invitations is None:
        raise HTTPException(status_code=403, detail="Sin permisos")
    return invitations


@router.post("/{workspace_id}/invitations")
async def invite_member(
    workspace_id: str,
    body: Dict[str, Any],
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)
    username = str(body.get("username") or "").strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="El username es obligatorio")

    def _sync():
        from app.auth.auth import get_user_by_username
        if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            return "forbidden", None
        if not _ws.get(workspace_id):
            return "no_ws", None
        if not get_user_by_username(username):
            return "no_user", None
        if _ws.is_member(workspace_id, username):
            return "already_member", None
        inv = _ws.invite_user(workspace_id, username, ctx.user)
        if inv is None:
            return "pending", None
        return "ok", inv

    status, inv = await run_db(_sync)
    if status == "forbidden":
        raise HTTPException(status_code=403, detail="Sin permisos para invitar miembros")
    if status == "no_ws":
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if status == "no_user":
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if status == "already_member":
        raise HTTPException(status_code=409, detail="El usuario ya es miembro de este workspace")
    if status == "pending":
        raise HTTPException(status_code=409, detail="Ya existe una invitacion pendiente para este usuario")
    return inv


@router.delete("/{workspace_id}/invitations/{inv_id}")
async def cancel_workspace_invitation(
    workspace_id: str,
    inv_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _assert_not_guest(ctx.user)

    def _sync():
        if not _ws.can_manage(workspace_id, ctx.user) and get_user_role(ctx.user) != "admin":
            return "forbidden"
        if not _ws.cancel_invitation(inv_id, workspace_id):
            return "not_found"
        return "ok"

    result = await run_db(_sync)
    if result == "forbidden":
        raise HTTPException(status_code=403, detail="Sin permisos")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Invitacion no encontrada")
    return {"ok": True}


@router.post("/{workspace_id}/transfer-ownership")
async def transfer_workspace_ownership(
    workspace_id: str,
    request: Request,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Transfiere la propiedad del workspace a otro miembro existente."""
    body = await request.json()
    new_owner = str(body.get("username", "")).strip()
    if not new_owner:
        raise HTTPException(status_code=400, detail="Se requiere 'username' del nuevo propietario")
    if new_owner == username:
        raise HTTPException(status_code=400, detail="Ya eres el propietario")

    def _sync():
        ws = _ws.get(workspace_id)
        if not ws:
            return "no_ws"
        if ws.get("created_by") != username and get_user_role(username) != "admin":
            return "forbidden"
        if not _ws.transfer_ownership(workspace_id, new_owner):
            return "not_member"
        return "ok"

    result = await run_db(_sync)
    if result == "no_ws":
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    if result == "forbidden":
        raise HTTPException(status_code=403, detail="Solo el propietario puede transferir el workspace")
    if result == "not_member":
        raise HTTPException(status_code=400, detail="El usuario no es miembro de este workspace")
    return {"ok": True}
