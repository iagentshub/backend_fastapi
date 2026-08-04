"""Rutas de historial de conversaciones."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query, Request

from app.api.routes.auth import require_auth
from app.config.data import DB_FILE
from app.errors import APIError
from app.storage.chat import ChatStorage
from app.storage.guest import is_guest
from app.utils.net import json_body

router = APIRouter(prefix="/api/chats", tags=["chats"])
_chat = ChatStorage(DB_FILE)


@router.get("/recent")
async def list_recent_conversations(
    limit: int = Query(default=8, ge=1, le=50),
    user: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    if is_guest(user):
        return []
    return await _chat.list_recent_conversations(user, limit)


@router.get("/{agent_id}")
async def list_conversations(
    agent_id: str, user: str = Depends(require_auth)
) -> List[Dict[str, Any]]:
    if is_guest(user):
        return []
    return await _chat.list_conversations(user, agent_id)


@router.post("/{agent_id}")
async def new_conversation(
    agent_id: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        raise APIError(403, "forbidden", "Los invitados no pueden guardar conversaciones")
    body = await json_body(request)
    title = str(body.get("title") or "")
    return await _chat.new_conversation(user, agent_id, title)


@router.get("/{agent_id}/{conv_id}")
async def get_messages(
    agent_id: str, conv_id: str, user: str = Depends(require_auth)
) -> List[Dict[str, Any]]:
    if is_guest(user):
        return []
    conv = await _chat.get_conversation(conv_id, user)
    if not conv:
        raise APIError(404, "not_found", "Conversación no encontrada", extra={"resource": "conversation"})
    return await _chat.get_messages(conv_id, user)


@router.delete("/{agent_id}/{conv_id}")
async def delete_conversation(
    agent_id: str, conv_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        raise APIError(403, "forbidden", "Los invitados no pueden borrar conversaciones")
    if not await _chat.delete_conversation(conv_id, user):
        raise APIError(404, "not_found", "Conversación no encontrada", extra={"resource": "conversation"})
    return {"ok": True}
