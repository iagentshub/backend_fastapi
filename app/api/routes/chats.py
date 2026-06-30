"""Rutas de historial de conversaciones."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.config.data import DB_FILE
from app.storage.chat import ChatStorage
from app.storage.guest import is_guest

router = APIRouter(prefix="/api/chats", tags=["chats"])
_chat = ChatStorage(DB_FILE)


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
        raise HTTPException(status_code=403, detail="Los invitados no pueden guardar conversaciones")
    body = await request.json()
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
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return await _chat.get_messages(conv_id, user)


@router.delete("/{agent_id}/{conv_id}")
async def delete_conversation(
    agent_id: str, conv_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if is_guest(user):
        raise HTTPException(status_code=403, detail="Los invitados no pueden borrar conversaciones")
    if not await _chat.delete_conversation(conv_id, user):
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return {"ok": True}
