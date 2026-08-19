"""Webhook de Stripe: la suscripción también cambia fuera de nuestra API.

Exento de la comprobación anti-CSRF por venir sin `Origin` ni `Referer`; lo que
lo autentica es la firma de Stripe.
"""


from __future__ import annotations

from typing import Any, Dict

import stripe
from fastapi import Request

from app.api.routes.billing._shared import (
    _billing,
    _extract_period_end,
    _safe_get,
    router,
)
from app.auth.auth import get_user_by_username
from app.config.billing import (
    STRIPE_WEBHOOK_SECRET,
)
from app.errors import APIError
from app.services.billing_link import (
    get_username_by_stripe_customer_id,
)

_HANDLED_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}

@router.post("/webhook")
async def webhook(request: Request) -> Dict[str, Any]:
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise APIError(400, "invalid_webhook_signature", "Firma inválida")

    event_id = event["id"]
    if await _billing.has_processed_event(event_id):
        return {"received": True}

    event_type = event["type"]
    if event_type in _HANDLED_EVENTS:
        await _handle_subscription_event(event["data"]["object"], event_type)

    event_dict = event.to_dict() if hasattr(event, "to_dict") else event
    await _billing.record_event(event_id, event_type, event_dict)
    return {"received": True}

async def _handle_subscription_event(sub: Dict[str, Any], event_type: str) -> None:
    metadata = _safe_get(sub, "metadata") or {}
    user_id = _safe_get(metadata, "user_id")
    if not user_id:
        legacy_username = _safe_get(metadata, "username")
        legacy_user = (
            await get_user_by_username(legacy_username) if legacy_username else None
        )
        user_id = legacy_user["id"] if legacy_user else None
    if not user_id:
        user_id = await get_username_by_stripe_customer_id(sub["customer"])
    if not user_id:
        return  # No se puede atribuir a un usuario — no debería ocurrir en flujo normal

    tier = _safe_get(metadata, "tier") or "developer"
    try:
        seats = int(_safe_get(metadata, "seats", 1))
    except (TypeError, ValueError):
        seats = 1
    interval = _safe_get(metadata, "interval") or "month"
    self_hosted = _safe_get(metadata, "self_hosted") == "1"

    existing = await _billing.get_by_stripe_subscription_id(sub["id"])
    amount_cents = existing["amount_cents"] if existing else 0

    status = (
        "canceled" if event_type == "customer.subscription.deleted" else sub["status"]
    )

    saved = await _billing.upsert(
        username=user_id,
        stripe_customer_id=sub["customer"],
        stripe_subscription_id=sub["id"],
        tier=tier,
        seats=seats,
        self_hosted=self_hosted,
        interval=interval,
        amount_cents=amount_cents,
        status=status,
        current_period_end=_extract_period_end(sub),
        cancel_at_period_end=bool(_safe_get(sub, "cancel_at_period_end", False)),
    )
    await _billing.ensure_owner_license(saved)
