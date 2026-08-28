"""Avisos del usuario: listarlos y marcarlos leídos."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.routes.auth import require_auth
from app.errors import APIError
from app.services.push import clave_publica, push_disponible
from app.storage import push_subscriptions as _subs
from app.storage.notifications import (
    count_unread,
    list_notifications,
    mark_all_read,
    mark_read,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class MarkReadBody(BaseModel):
    """Sin `id` marca todas: es lo que hace abrir el desplegable."""

    id: Optional[str] = None


@router.get("")
async def list_all(user: str = Depends(require_auth)) -> Dict[str, Any]:
    """La lista y el contador en la misma respuesta.

    ponytail: un solo endpoint, no uno aparte para el número. El cliente sondea
    cada 60 s y de aquí saca las dos cosas; si el payload llega a pesar, se
    parte en un `/count` barato.
    """
    return {
        "items": await list_notifications(user),
        "unread": await count_unread(user),
    }


@router.post("/read")
async def mark(
    body: MarkReadBody, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    notification_id = (body.id or "").strip()
    if notification_id:
        await mark_read(user, notification_id)
    else:
        await mark_all_read(user)
    # Se devuelve el contador ya actualizado para que el cliente no encadene
    # otra petición solo para bajar el número del badge.
    return {"ok": True, "unread": await count_unread(user)}


class PushSubscription(BaseModel):
    """Lo que entrega `PushManager.subscribe()` en el navegador."""

    endpoint: str = Field(min_length=1, max_length=2000)
    p256dh: str = Field(default="", max_length=200)
    auth: str = Field(default="", max_length=200)


@router.get("/push/key")
async def push_key(_: str = Depends(require_auth)) -> Dict[str, Any]:
    """La clave pública VAPID, o `null` si esta instalación no tiene push.

    El cliente la pide antes de ofrecer el botón de activar avisos: sin clave
    no hay nada que activar, y enseñar un interruptor que no funciona es peor
    que no enseñarlo.
    """
    return {"key": clave_publica() or None, "enabled": push_disponible()}


@router.post("/push/subscribe")
async def push_subscribe(
    body: PushSubscription,
    request: Request,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    if not push_disponible():
        raise APIError(
            503,
            "push_unavailable",
            "Esta instalación no tiene las notificaciones push configuradas",
        )
    # El endpoint lo emite el servicio push del navegador, nunca el usuario,
    # pero llega por una petición: si no es una URL https no se guarda.
    if not body.endpoint.startswith("https://"):
        raise APIError(
            422,
            "invalid_field",
            "El endpoint de push debe ser una URL https",
            extra={"field": "endpoint"},
        )
    await _subs.subscribe(
        user_id=user,
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"ok": True, "devices": await _subs.count_for_user(user)}


@router.delete("/push/subscribe")
async def push_unsubscribe(
    body: PushSubscription, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Baja de este navegador. No toca los demás dispositivos del usuario."""
    await _subs.unsubscribe(body.endpoint)
    return {"ok": True, "devices": await _subs.count_for_user(user)}
