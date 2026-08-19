"""Alta, cambio y baja de la suscripción, y su estado."""


from __future__ import annotations

from typing import Any, Dict

import stripe
from fastapi import Depends
from pydantic import BaseModel, Field

from app.api.routes.auth import require_auth
from app.api.routes.billing._shared import (
    _SELF_HOSTED_PRICE_IDS,
    _billing,
    _extract_period_end,
    _free_state,
    _quote_limiter,
    _row_to_state,
    _safe_get,
    _subscribe_limiter,
    router,
)
from app.auth.auth import get_user_by_id
from app.config.billing import (
    STRIPE_PRODUCT_SEATS,
)
from app.errors import APIError
from app.services.billing_link import (
    get_stripe_customer_id,
    set_stripe_customer_id,
)
from app.services.billing_pricing import InvalidPlanError, compute_total_cents


class PlanBody(BaseModel):
    tier: str = Field(default="", max_length=32)
    seats: int = Field(default=0, ge=0, le=10_000)
    interval: str = Field(default="", max_length=16)
    self_hosted: bool = False

class ConfirmBody(BaseModel):
    subscription_id: str = Field(default="", max_length=255)

class ChangeSeatsBody(BaseModel):
    seats: int = Field(default=0, ge=0, le=10_000)

class CancelBody(BaseModel):
    immediate: bool = False

def _parse_body_plan(body: PlanBody) -> tuple[str, int, str, bool]:
    return body.tier, body.seats, body.interval, body.self_hosted

@router.post("/quote")
async def quote(
    body: PlanBody,
    _rl: None = Depends(_quote_limiter),
) -> Dict[str, Any]:
    # Sin auth a propósito: solo calcula un precio a partir de datos públicos y
    # la página de precios lo consulta antes de que exista sesión. Lo que sí
    # faltaba es el límite: era el único POST de billing que cualquiera podía
    # llamar sin freno.
    tier, seats, interval, self_hosted = _parse_body_plan(body)
    try:
        return compute_total_cents(tier, seats, interval, self_hosted)
    except InvalidPlanError as exc:
        raise APIError(400, "invalid_field", str(exc), extra={"field": "plan"})

@router.post("/subscribe")
async def subscribe(
    body: PlanBody,
    user: str = Depends(require_auth),
    _rl: None = Depends(_subscribe_limiter),
) -> Dict[str, Any]:
    tier, seats, interval, self_hosted = _parse_body_plan(body)
    try:
        totals = compute_total_cents(tier, seats, interval, self_hosted)
    except InvalidPlanError as exc:
        raise APIError(400, "invalid_field", str(exc), extra={"field": "plan"})

    if await _billing.get_active_by_username(user):
        raise APIError(
            409, "subscription_already_active", "Ya tienes una suscripción activa"
        )

    customer_id = await get_stripe_customer_id(user)
    try:
        if not customer_id:
            user_row = await get_user_by_id(user)
            customer = stripe.Customer.create(
                email=(user_row or {}).get("email") or None,
                metadata={"user_id": user},
            )
            customer_id = customer.id
            await set_stripe_customer_id(user, customer_id)

        items = [
            {
                "price_data": {
                    "currency": "eur",
                    "product": STRIPE_PRODUCT_SEATS,
                    "unit_amount": totals["seats_amount_cents"],
                    "recurring": {"interval": interval},
                },
                "quantity": 1,
            }
        ]
        if self_hosted:
            sh_price_id = _SELF_HOSTED_PRICE_IDS.get(interval)
            if not sh_price_id:
                raise APIError(
                    400,
                    "self_hosted_addon_not_configured",
                    "Add-on self-hosted no configurado",
                )
            items.append({"price": sh_price_id, "quantity": 1})

        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=items,
            payment_behavior="default_incomplete",
            payment_settings={"save_default_payment_method": "on_subscription"},
            expand=["latest_invoice.payment_intent"],
            metadata={
                "user_id": user,
                "tier": tier,
                "seats": str(seats),
                "interval": interval,
                "self_hosted": "1" if self_hosted else "0",
            },
        )
    except stripe.error.CardError as exc:
        raise APIError(400, "upstream_error", str(exc))
    except stripe.error.StripeError as exc:
        raise APIError(502, "upstream_error", str(exc))

    current_period_end = _extract_period_end(subscription)
    saved = await _billing.upsert(
        username=user,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription.id,
        tier=tier,
        seats=seats,
        self_hosted=self_hosted,
        interval=interval,
        amount_cents=totals["amount_cents"],
        status=subscription.status,
        current_period_end=current_period_end,
        cancel_at_period_end=False,
    )
    await _billing.ensure_owner_license(saved)

    return {
        "subscription_id": subscription.id,
        "client_secret": subscription.latest_invoice.payment_intent.client_secret,
        "status": subscription.status,
    }

@router.post("/confirm")
async def confirm(
    body: ConfirmBody, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    subscription_id = body.subscription_id
    if not subscription_id:
        raise APIError(
            400,
            "invalid_field",
            "Valor inválido: subscription_id",
            extra={"field": "subscription_id"},
        )

    customer_id = await get_stripe_customer_id(user)
    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
    except stripe.error.StripeError as exc:
        raise APIError(502, "upstream_error", str(exc))

    if not customer_id or subscription.customer != customer_id:
        raise APIError(403, "forbidden", "No autorizado")

    row = await _billing.get_by_stripe_subscription_id(subscription_id)
    if not row:
        raise APIError(
            404,
            "not_found",
            "Suscripción no encontrada",
            extra={"resource": "subscription"},
        )

    saved = await _billing.upsert(
        username=user,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        tier=row["tier"],
        seats=row["seats"],
        self_hosted=bool(row["self_hosted"]),
        interval=row["interval"],
        amount_cents=row["amount_cents"],
        status=subscription.status,
        current_period_end=_extract_period_end(subscription),
        cancel_at_period_end=bool(
            _safe_get(subscription, "cancel_at_period_end", False)
        ),
    )
    await _billing.ensure_owner_license(saved)
    updated = await _billing.get_by_stripe_subscription_id(subscription_id)
    return _row_to_state(updated)  # type: ignore[arg-type]

@router.get("/subscription")
async def get_subscription(user: str = Depends(require_auth)) -> Dict[str, Any]:
    row = await _billing.get_active_by_username(user)
    if not row:
        return _free_state()
    return _row_to_state(row)

@router.post("/change-seats")
async def change_seats(
    body: ChangeSeatsBody, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    seats = body.seats

    row = await _billing.get_active_by_username(user)
    if not row:
        raise APIError(
            404, "no_active_subscription", "No tienes una suscripción activa"
        )
    if row["tier"] != "business":
        raise APIError(
            400,
            "business_tier_required",
            "Solo el plan Business admite cambiar asientos",
            extra={"action": "change_seats"},
        )

    used = await _billing.assigned_count(row["id"])
    if seats < used:
        raise APIError(
            409,
            "seats_below_assigned",
            f"No puedes bajar a {seats} licencias: ya hay {used} asignadas",
            extra={"seats": seats, "used": used},
        )

    try:
        totals = compute_total_cents(
            "business", seats, row["interval"], bool(row["self_hosted"])
        )
    except InvalidPlanError as exc:
        raise APIError(400, "invalid_field", str(exc), extra={"field": "plan"})

    try:
        subscription = stripe.Subscription.retrieve(row["stripe_subscription_id"])
        seat_item = subscription["items"]["data"][0]
        stripe.SubscriptionItem.modify(
            seat_item["id"],
            price_data={
                "currency": "eur",
                "product": STRIPE_PRODUCT_SEATS,
                "unit_amount": totals["seats_amount_cents"],
                "recurring": {"interval": row["interval"]},
            },
            quantity=1,
            proration_behavior="create_prorations",
        )
    except stripe.error.StripeError as exc:
        raise APIError(502, "upstream_error", str(exc))

    updated = await _billing.upsert(
        username=user,
        stripe_customer_id=row["stripe_customer_id"],
        stripe_subscription_id=row["stripe_subscription_id"],
        tier="business",
        seats=seats,
        self_hosted=bool(row["self_hosted"]),
        interval=row["interval"],
        amount_cents=totals["amount_cents"],
        status=row["status"],
        current_period_end=row["current_period_end"],
        cancel_at_period_end=bool(row["cancel_at_period_end"]),
    )
    return _row_to_state(updated)  # type: ignore[arg-type]

@router.post("/cancel")
async def cancel(body: CancelBody, user: str = Depends(require_auth)) -> Dict[str, Any]:
    immediate = body.immediate

    row = await _billing.get_active_by_username(user)
    if not row:
        raise APIError(
            404, "no_active_subscription", "No tienes una suscripción activa"
        )

    try:
        if immediate:
            stripe.Subscription.delete(row["stripe_subscription_id"])
        else:
            stripe.Subscription.modify(
                row["stripe_subscription_id"], cancel_at_period_end=True
            )
    except stripe.error.StripeError as exc:
        raise APIError(502, "upstream_error", str(exc))

    updated = await _billing.upsert(
        username=user,
        stripe_customer_id=row["stripe_customer_id"],
        stripe_subscription_id=row["stripe_subscription_id"],
        tier=row["tier"],
        seats=row["seats"],
        self_hosted=bool(row["self_hosted"]),
        interval=row["interval"],
        amount_cents=row["amount_cents"],
        status="canceled" if immediate else row["status"],
        current_period_end=row["current_period_end"],
        cancel_at_period_end=True,
    )
    return _row_to_state(updated)  # type: ignore[arg-type]

@router.post("/reactivate")
async def reactivate(user: str = Depends(require_auth)) -> Dict[str, Any]:
    row = await _billing.get_by_username(user)
    if not row:
        raise APIError(404, "no_subscription", "No tienes una suscripción")
    if row["status"] == "canceled":
        raise APIError(
            400,
            "subscription_already_canceled",
            "La suscripción ya está cancelada, debes suscribirte de nuevo",
        )
    if not row["cancel_at_period_end"]:
        raise APIError(
            400,
            "no_pending_cancellation",
            "La suscripción no tiene cancelación pendiente",
        )

    try:
        stripe.Subscription.modify(
            row["stripe_subscription_id"], cancel_at_period_end=False
        )
    except stripe.error.StripeError as exc:
        raise APIError(502, "upstream_error", str(exc))

    await _billing.set_cancel_at_period_end(row["stripe_subscription_id"], False)
    updated = await _billing.get_by_stripe_subscription_id(
        row["stripe_subscription_id"]
    )
    return _row_to_state(updated)  # type: ignore[arg-type]

@router.get("/invoices")
async def invoices(user: str = Depends(require_auth)) -> Dict[str, Any]:
    customer_id = await get_stripe_customer_id(user)
    if not customer_id:
        return {"invoices": []}
    try:
        result = stripe.Invoice.list(customer=customer_id, limit=12)
    except stripe.error.StripeError as exc:
        raise APIError(502, "upstream_error", str(exc))
    return {
        "invoices": [
            {
                "id": inv["id"],
                "amount_paid": inv["amount_paid"],
                "currency": inv["currency"],
                "status": inv["status"],
                "created": inv["created"],
                "hosted_invoice_url": _safe_get(inv, "hosted_invoice_url"),
                "invoice_pdf": _safe_get(inv, "invoice_pdf"),
            }
            for inv in _safe_get(result, "data", [])
        ]
    }
