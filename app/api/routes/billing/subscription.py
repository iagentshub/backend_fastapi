"""Alta, cambio y baja de la suscripción, y su estado."""


from __future__ import annotations

from typing import Any, Dict

import stripe
from fastapi import Depends
from pydantic import BaseModel, Field

from app.api.routes.auth import require_auth
from app.api.routes.billing._shared import (
    SLA_SUPPORT_PRICE_ID,
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
    STRIPE_TAX_BEHAVIOR,
    STRIPE_TAX_ENABLED,
)
from app.errors import APIError
from app.services.billing_link import (
    get_stripe_customer_id,
    set_stripe_customer_id,
)
from app.services.billing_pricing import InvalidPlanError, compute_total_cents
from app.services.billing_tax import (
    TaxIdentityError,
    normalize_country,
    normalize_tax_id,
)
from app.utils import flog


class PlanBody(BaseModel):
    tier: str = Field(default="", max_length=32)
    seats: int = Field(default=0, ge=0, le=10_000)
    interval: str = Field(default="", max_length=16)
    # `self_hosted` es como se llamaba el add-on cuando el nombre describía
    # un permiso de despliegue. Se sigue aceptando porque un bundle cacheado
    # lo manda; el que vale es `sla_support`, y se puede retirar cuando los
    # dos clientes hayan salido.
    sla_support: bool | None = None
    self_hosted: bool = False
    # País de facturación (ISO 3166-1 alfa-2) y NIF-IVA. /quote los ignora
    # —calcula el importe sin impuestos, que no depende de quién pregunte— y
    # /subscribe exige el país: sin él Stripe no puede calcular el IVA.
    country: str = Field(default="", max_length=2)
    tax_id: str = Field(default="", max_length=32)

class ConfirmBody(BaseModel):
    subscription_id: str = Field(default="", max_length=255)

class ChangeSeatsBody(BaseModel):
    seats: int = Field(default=0, ge=0, le=10_000)

class CancelBody(BaseModel):
    immediate: bool = False

def _parse_body_plan(body: PlanBody) -> tuple[str, int, str, bool]:
    sla = body.self_hosted if body.sla_support is None else body.sla_support
    return body.tier, body.seats, body.interval, sla

# Códigos de error de Stripe que son culpa de lo que ha escrito el cliente, no
# de la instalación: llegaban como 502 «upstream_error» con el texto crudo de
# la pasarela, que el cliente no sabe traducir y que además no dice qué campo
# corregir. Ver docs/es/config.md, «Impuestos de las suscripciones».
_TAX_ERROR_CODES: Dict[str, tuple[str, str, str]] = {
    "customer_tax_location_invalid": (
        "invalid_tax_location",
        "No podemos calcular el impuesto para ese país. Revisa tu país de facturación.",
        "country",
    ),
    "tax_id_invalid": (
        "invalid_tax_id",
        "El NIF-IVA no es válido. Compruébalo o déjalo vacío para pagar con IVA.",
        "tax_id",
    ),
}


def _stripe_error(exc: stripe.error.StripeError) -> APIError:
    """Traduce a error propio lo que el cliente puede arreglar; el resto, 502.

    `customer_tax_location_invalid` también aparece cuando la cuenta de Stripe
    no tiene declarada la obligación fiscal del país: es la misma respuesta
    para «el cliente escribió mal su país» y para «falta configurar Stripe
    Tax». Se distingue en el log de arranque, no aquí.
    """
    code = getattr(exc, "code", "") or ""
    mapped = _TAX_ERROR_CODES.get(code)
    if mapped:
        api_code, message, field = mapped
        return APIError(400, api_code, message, extra={"field": field})
    return APIError(502, "upstream_error", str(exc))


def _apply_tax_identity(customer_id: str, country: str, tax_id: tuple[str, str] | None) -> None:
    """Deja en el cliente de Stripe la dirección y el NIF con los que facturar.

    El país se reescribe en cada alta aunque el cliente ya existiese: es el que
    acaba de declarar, y es el que va a salir en la factura. El NIF solo se
    crea si no lo tenía ya — Stripe admite duplicados sin quejarse y la factura
    acabaría con el mismo número repetido.
    """
    stripe.Customer.modify(customer_id, address={"country": country})
    if tax_id is None:
        return
    tipo, valor = tax_id
    existentes = stripe.Customer.list_tax_ids(customer_id, limit=20)
    for registrado in _safe_get(existentes, "data", []) or []:
        if _safe_get(registrado, "value") == valor:
            return
    stripe.Customer.create_tax_id(customer_id, type=tipo, value=valor)


def _invoice_totals(subscription: Any, neto_cents: int) -> Dict[str, int]:
    """Importe real que se va a cobrar, tomado de la factura que Stripe ya creó.

    `payment_behavior="default_incomplete"` deja el borrador de factura hecho
    con el impuesto ya calculado, así que el desglose no cuesta ninguna llamada
    extra. Sin Stripe Tax —o con una factura que aún no trae los totales— el
    impuesto es 0 y el total es el neto: lo que se cobraba antes.
    """
    invoice = _safe_get(subscription, "latest_invoice") or {}
    subtotal = _safe_get(invoice, "subtotal")
    total = _safe_get(invoice, "total")
    if subtotal is None or total is None:
        return {"subtotal_cents": neto_cents, "tax_cents": 0, "total_cents": neto_cents}
    impuesto = _safe_get(invoice, "tax")
    if impuesto is None:
        # Versiones de la API en las que el campo pasó a `total_taxes`: la
        # diferencia es la misma cifra y no depende de cómo se llame.
        impuesto = total - subtotal
    return {
        "subtotal_cents": int(subtotal),
        "tax_cents": int(impuesto),
        "total_cents": int(total),
    }

async def _descartar_incompleta(user: str, row: Dict[str, Any]) -> None:
    """Tira una suscripción `incomplete` para poder empezar el alta de nuevo.

    `incomplete` es un alta que se creó y nunca se pagó: el que abandonó el
    checkout y volvió, y —desde el IVA— el que se equivocó de país y pulsa
    «cambiar». Contaba como activa, así que el segundo intento chocaba con un
    409 hablando de una suscripción que el usuario nunca llegó a tener.

    No se puede reutilizar la anterior: su factura ya lleva calculado el
    impuesto del país que se declaró entonces, que es justo lo que se está
    cambiando.

    Marcarla `canceled` no es cosmético — `has_active_license` da acceso por
    cualquier suscripción que no esté cancelada o expirada, `incomplete`
    incluida.
    """
    try:
        stripe.Subscription.delete(row["stripe_subscription_id"])
    except stripe.error.StripeError as exc:
        # Que ya no exista en Stripe es el caso bueno: lo que no puede es
        # impedir el alta nueva. Queda en el log por si fuese otra cosa.
        flog.warning(
            f"[billing] no se pudo cancelar la suscripción incompleta "
            f"{row['stripe_subscription_id']}: {exc}"
        )
    await _billing.upsert(
        username=user,
        stripe_customer_id=row["stripe_customer_id"],
        stripe_subscription_id=row["stripe_subscription_id"],
        tier=row["tier"],
        seats=row["seats"],
        self_hosted=bool(row["self_hosted"]),
        interval=row["interval"],
        amount_cents=row["amount_cents"],
        status="canceled",
        current_period_end=row["current_period_end"],
        cancel_at_period_end=True,
    )


@router.post("/quote")
async def quote(
    body: PlanBody,
    _rl: None = Depends(_quote_limiter),
) -> Dict[str, Any]:
    # Sin auth a propósito: solo calcula un precio a partir de datos públicos y
    # la página de precios lo consulta antes de que exista sesión. Lo que sí
    # faltaba es el límite: era el único POST de billing que cualquiera podía
    # llamar sin freno.
    tier, seats, interval, sla_support = _parse_body_plan(body)
    try:
        totals = compute_total_cents(tier, seats, interval, sla_support)
    except InvalidPlanError as exc:
        raise APIError(400, "invalid_field", str(exc), extra={"field": "plan"})
    # El importe sigue siendo el neto: el impuesto depende del país, que aquí
    # todavía no se conoce. Lo que se añade es cómo hay que anunciarlo, para
    # que la página de precios no tenga que llevar la política duplicada.
    totals["tax_behavior"] = STRIPE_TAX_BEHAVIOR if STRIPE_TAX_ENABLED else "none"
    return totals

@router.post("/subscribe")
async def subscribe(
    body: PlanBody,
    user: str = Depends(require_auth),
    _rl: None = Depends(_subscribe_limiter),
) -> Dict[str, Any]:
    tier, seats, interval, sla_support = _parse_body_plan(body)
    try:
        totals = compute_total_cents(tier, seats, interval, sla_support)
    except InvalidPlanError as exc:
        raise APIError(400, "invalid_field", str(exc), extra={"field": "plan"})

    # Antes de tocar Stripe: el país es obligatorio aunque los impuestos estén
    # apagados, porque es la dirección con la que se emite la factura.
    try:
        country = normalize_country(body.country)
        tax_id = normalize_tax_id(body.tax_id, country)
    except TaxIdentityError as exc:
        raise APIError(400, exc.code, exc.message, extra={"field": exc.field})

    activa = await _billing.get_active_by_username(user)
    if activa and activa["status"] != "incomplete":
        raise APIError(
            409, "subscription_already_active", "Ya tienes una suscripción activa"
        )
    if activa:
        await _descartar_incompleta(user, activa)

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

        _apply_tax_identity(customer_id, country, tax_id)

        precio_asientos: Dict[str, Any] = {
            "currency": "eur",
            "product": STRIPE_PRODUCT_SEATS,
            "unit_amount": totals["seats_amount_cents"],
            "recurring": {"interval": interval},
        }
        if STRIPE_TAX_ENABLED:
            # Obligatorio con automatic_tax: sin decir si el importe lleva el
            # impuesto dentro o fuera, Stripe rechaza la creación entera.
            precio_asientos["tax_behavior"] = STRIPE_TAX_BEHAVIOR

        items = [{"price_data": precio_asientos, "quantity": 1}]
        if sla_support:
            sla_price_id = SLA_SUPPORT_PRICE_ID
            if not sla_price_id:
                raise APIError(
                    400,
                    "sla_support_addon_not_configured",
                    "Add-on de soporte con SLA no configurado",
                )
            items.append({"price": sla_price_id, "quantity": 1})

        extra: Dict[str, Any] = (
            {"automatic_tax": {"enabled": True}} if STRIPE_TAX_ENABLED else {}
        )
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
                "sla_support": "1" if sla_support else "0",
                "country": country,
            },
            **extra,
        )
    except stripe.error.CardError as exc:
        raise APIError(400, "upstream_error", str(exc))
    except stripe.error.StripeError as exc:
        raise _stripe_error(exc)

    current_period_end = _extract_period_end(subscription)
    saved = await _billing.upsert(
        username=user,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription.id,
        tier=tier,
        seats=seats,
        self_hosted=sla_support,
        interval=interval,
        amount_cents=totals["amount_cents"],
        status=subscription.status,
        current_period_end=current_period_end,
        cancel_at_period_end=False,
    )
    await _billing.ensure_owner_license(saved)

    # El desglose sale de la factura que Stripe acaba de crear, no de nuestra
    # aritmética: es exactamente lo que se va a cobrar, impuesto incluido, y el
    # checkout tiene que enseñarlo antes de que el usuario confirme el pago.
    return {
        "subscription_id": subscription.id,
        "client_secret": subscription.latest_invoice.payment_intent.client_secret,
        "status": subscription.status,
        **_invoice_totals(subscription, totals["amount_cents"]),
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
            f"No puedes bajar a {seats} asientos: ya hay {used} asignados",
            extra={"seats": seats, "used": used},
        )

    try:
        totals = compute_total_cents(
            "business",
            seats,
            row["interval"],
            bool(row["self_hosted"]),
            new_contract=False,
        )
    except InvalidPlanError as exc:
        raise APIError(400, "invalid_field", str(exc), extra={"field": "plan"})

    # Mismo `tax_behavior` que en el alta: el precio se sustituye entero, y uno
    # nuevo sin él sobre una suscripción con automatic_tax la deja sin poder
    # facturar el siguiente periodo.
    precio_asientos: Dict[str, Any] = {
        "currency": "eur",
        "product": STRIPE_PRODUCT_SEATS,
        "unit_amount": totals["seats_amount_cents"],
        "recurring": {"interval": row["interval"]},
    }
    if STRIPE_TAX_ENABLED:
        precio_asientos["tax_behavior"] = STRIPE_TAX_BEHAVIOR

    try:
        subscription = stripe.Subscription.retrieve(row["stripe_subscription_id"])
        seat_item = subscription["items"]["data"][0]
        stripe.SubscriptionItem.modify(
            seat_item["id"],
            price_data=precio_asientos,
            quantity=1,
            proration_behavior="create_prorations",
        )
    except stripe.error.StripeError as exc:
        raise _stripe_error(exc)

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
