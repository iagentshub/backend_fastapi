"""Rutas de facturación — suscripciones Stripe (developer/business, self-serve)."""

from __future__ import annotations

from typing import Any, Dict, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.auth.auth import (
    get_stripe_customer_id,
    get_user_by_id,
    get_user_by_username,
    get_username_by_stripe_customer_id,
    set_stripe_customer_id,
)
from app.config.billing import (
    STRIPE_API_VERSION,
    STRIPE_PRICE_SELFHOSTED_ANNUAL,
    STRIPE_PRICE_SELFHOSTED_MONTHLY,
    STRIPE_PRODUCT_SEATS,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from app.config.data import DB_FILE
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.services.billing_pricing import InvalidPlanError, compute_total_cents
from app.storage.billing import BillingStorage
from app.utils.net import client_ip as _client_ip

stripe.api_key = STRIPE_SECRET_KEY
stripe.api_version = STRIPE_API_VERSION

router = APIRouter(prefix="/api/billing", tags=["billing"])

_billing = BillingStorage(DB_FILE)
_subscribe_limiter = RateLimiter(calls=10, window=60, key_func=_client_ip)

_SELF_HOSTED_PRICE_IDS = {
    "month": STRIPE_PRICE_SELFHOSTED_MONTHLY,
    "year": STRIPE_PRICE_SELFHOSTED_ANNUAL,
}


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """dict-and-StripeObject-safe lookup — real Stripe SDK objects don't implement .get()."""
    try:
        return obj[key]
    except (KeyError, TypeError):
        return default


def _free_state() -> Dict[str, Any]:
    return {
        "tier": "free",
        "seats": 0,
        "self_hosted": False,
        "interval": None,
        "status": None,
        "current_period_end": None,
        "cancel_at_period_end": False,
    }


def _row_to_state(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tier": row["tier"],
        "seats": row["seats"],
        "self_hosted": bool(row["self_hosted"]),
        "interval": row["interval"],
        "status": row["status"],
        "current_period_end": row["current_period_end"],
        "cancel_at_period_end": bool(row["cancel_at_period_end"]),
    }


def _license_error(exc: ValueError) -> HTTPException:
    code = str(exc)
    messages = {
        "subscription_not_active": "No tienes una suscripción activa",
        "user_not_found": "Usuario no encontrado",
        "license_already_assigned": "La licencia ya está asignada a este usuario",
        "no_seats_available": "No hay asientos disponibles en tu plan",
    }
    status = {
        "subscription_not_active": 404,
        "user_not_found": 404,
        "license_already_assigned": 409,
        "no_seats_available": 409,
    }.get(code, 400)
    if code in messages:
        return APIError(status, code, messages[code])
    return APIError(status, "invalid_license_operation", code)


def _parse_body_plan(body: Dict[str, Any]) -> tuple[str, int, str, bool]:
    tier = str(body.get("tier") or "")
    interval = str(body.get("interval") or "")
    self_hosted = bool(body.get("self_hosted", False))
    try:
        seats = int(body.get("seats", 0))
    except (TypeError, ValueError):
        raise APIError(400, "invalid_field", "Valor inválido: seats", extra={"field": "seats"})
    return tier, seats, interval, self_hosted


@router.post("/quote")
async def quote(request: Request) -> Dict[str, Any]:
    body = await request.json()
    tier, seats, interval, self_hosted = _parse_body_plan(body)
    try:
        return compute_total_cents(tier, seats, interval, self_hosted)
    except InvalidPlanError as exc:
        raise APIError(400, "invalid_field", str(exc), extra={"field": "plan"})


@router.post("/subscribe")
async def subscribe(
    request: Request,
    user: str = Depends(require_auth),
    _rl: None = Depends(_subscribe_limiter),
) -> Dict[str, Any]:
    body = await request.json()
    tier, seats, interval, self_hosted = _parse_body_plan(body)
    try:
        totals = compute_total_cents(tier, seats, interval, self_hosted)
    except InvalidPlanError as exc:
        raise APIError(400, "invalid_field", str(exc), extra={"field": "plan"})

    if await _billing.get_active_by_username(user):
        raise APIError(409, "subscription_already_active", "Ya tienes una suscripción activa")

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
                    400, "self_hosted_addon_not_configured", "Add-on self-hosted no configurado"
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


def _extract_period_end(subscription: Any) -> Optional[str]:
    from datetime import datetime, timezone

    ts = _safe_get(subscription, "current_period_end")
    if ts is None:
        items = _safe_get(_safe_get(subscription, "items", {}), "data", [])
        if items:
            ts = _safe_get(items[0], "current_period_end")
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@router.post("/confirm")
async def confirm(request: Request, user: str = Depends(require_auth)) -> Dict[str, Any]:
    body = await request.json()
    subscription_id = str(body.get("subscription_id") or "")
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
        raise APIError(404, "not_found", "Suscripción no encontrada", extra={"resource": "subscription"})

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
        cancel_at_period_end=bool(_safe_get(subscription, "cancel_at_period_end", False)),
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


@router.get("/licenses")
async def get_licenses(user: str = Depends(require_auth)) -> Dict[str, Any]:
    return await _billing.license_summary_for_owner(user)


@router.post("/licenses/{username}")
async def assign_license(
    username: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    row = await _billing.get_active_by_username(user)
    if not row:
        raise APIError(404, "no_active_subscription", "No tienes una suscripción activa")
    if row["tier"] != "business":
        raise APIError(
            400,
            "business_tier_required",
            "Solo el plan Business permite asignar licencias",
            extra={"action": "assign_licenses"},
        )
    target = await get_user_by_username(username)
    if not target:
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    try:
        await _billing.assign_license(
            subscription_id=row["id"], target_username=target["id"], assigned_by=user
        )
    except ValueError as exc:
        raise _license_error(exc)
    return await _billing.license_summary_for_owner(user)


@router.delete("/licenses/{username}")
async def revoke_license(
    username: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    row = await _billing.get_active_by_username(user)
    if not row:
        raise APIError(404, "no_active_subscription", "No tienes una suscripción activa")
    if row["tier"] != "business":
        raise APIError(
            400,
            "business_tier_required",
            "Solo el plan Business permite quitar licencias",
            extra={"action": "revoke_licenses"},
        )
    target = await get_user_by_username(username)
    if not target:
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    target_id = target["id"]
    if target_id == user:
        raise APIError(400, "cannot_revoke_own_license", "No puedes quitar tu propia licencia")
    if not await _billing.revoke_license(subscription_id=row["id"], target_username=target_id):
        raise APIError(404, "not_found", "Licencia no encontrada", extra={"resource": "license"})
    return await _billing.license_summary_for_owner(user)


@router.post("/change-seats")
async def change_seats(request: Request, user: str = Depends(require_auth)) -> Dict[str, Any]:
    body = await request.json()
    try:
        seats = int(body.get("seats", 0))
    except (TypeError, ValueError):
        raise APIError(400, "invalid_field", "Valor inválido: seats", extra={"field": "seats"})

    row = await _billing.get_active_by_username(user)
    if not row:
        raise APIError(404, "no_active_subscription", "No tienes una suscripción activa")
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
        totals = compute_total_cents("business", seats, row["interval"], bool(row["self_hosted"]))
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
async def cancel(request: Request, user: str = Depends(require_auth)) -> Dict[str, Any]:
    body = await request.json()
    immediate = bool(body.get("immediate", False))

    row = await _billing.get_active_by_username(user)
    if not row:
        raise APIError(404, "no_active_subscription", "No tienes una suscripción activa")

    try:
        if immediate:
            stripe.Subscription.delete(row["stripe_subscription_id"])
        else:
            stripe.Subscription.modify(row["stripe_subscription_id"], cancel_at_period_end=True)
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
            400, "no_pending_cancellation", "La suscripción no tiene cancelación pendiente"
        )

    try:
        stripe.Subscription.modify(row["stripe_subscription_id"], cancel_at_period_end=False)
    except stripe.error.StripeError as exc:
        raise APIError(502, "upstream_error", str(exc))

    await _billing.set_cancel_at_period_end(row["stripe_subscription_id"], False)
    updated = await _billing.get_by_stripe_subscription_id(row["stripe_subscription_id"])
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
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
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
        legacy_user = await get_user_by_username(legacy_username) if legacy_username else None
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

    status = "canceled" if event_type == "customer.subscription.deleted" else sub["status"]

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
