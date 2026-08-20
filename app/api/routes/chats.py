"""Rutas de historial de conversaciones."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query, Response

from app.api.routes.auth import require_session
from app.errors import APIError
from app.models.request_bodies import ConversationBody
from app.pagination.http import publish_cursor_page
from app.storage.chat import ChatStorage

router = APIRouter(prefix="/api/chats", tags=["chats"])
_chat = ChatStorage()


@router.get("/recent")
async def list_recent_conversations(
    limit: int = Query(default=8, ge=1, le=50),
    user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    return await _chat.list_recent_conversations(user, limit)


@router.get("/{agent_id}")
async def list_conversations(
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    response: Response = None,  # type: ignore[assignment]
    user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    try:
        page = await _chat.list_conversations_page(
            user, agent_id, limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise APIError(422, "invalid_cursor", "Cursor no válido") from exc
    publish_cursor_page(response, page)
    return list(page.items)


@router.post("/{agent_id}")
async def new_conversation(
    agent_id: str, body: ConversationBody, user: str = Depends(require_session)
) -> Dict[str, Any]:
    body = body.payload()
    title = str(body.get("title") or "")
    return await _chat.new_conversation(user, agent_id, title)


@router.get("/{agent_id}/{conv_id}")
async def get_messages(
    agent_id: str,
    conv_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None),
    response: Response = None,  # type: ignore[assignment]
    user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    conv = await _chat.get_conversation(conv_id, user)
    if not conv:
        raise APIError(
            404,
            "not_found",
            "Conversación no encontrada",
            extra={"resource": "conversation"},
        )
    try:
        page = await _chat.get_messages_page(conv_id, user, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise APIError(422, "invalid_cursor", "Cursor no válido") from exc
    publish_cursor_page(response, page)
    return list(page.items)


@router.delete("/{agent_id}/{conv_id}")
async def delete_conversation(
    agent_id: str, conv_id: str, user: str = Depends(require_session)
) -> Dict[str, Any]:
    if not await _chat.delete_conversation(conv_id, user):
        raise APIError(
            404,
            "not_found",
            "Conversación no encontrada",
            extra={"resource": "conversation"},
        )
    return {"ok": True}
