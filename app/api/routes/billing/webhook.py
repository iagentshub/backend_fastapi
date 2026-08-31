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
from app.utils import flog

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
    event_type = event["type"]
    manejado = event_type in _HANDLED_EVENTS

    # El objeto entero solo de lo que se procesa. El id, el tipo y la fecha son
    # columnas, así que de un evento al que no hacemos nada no se pierde nada
    # que no siguiéramos teniendo — y la tabla crecía con todo lo que Stripe
    # mandase, no con lo que se maneja: cuanto más amplia la suscripción en su
    # panel, más datos de terceros guardados sin que nadie los mire.
    event_dict: Dict[str, Any] = {}
    if manejado:
        event_dict = event.to_dict() if hasattr(event, "to_dict") else event

    # Reservar antes de procesar, no después. La guarda anterior era un SELECT
    # y la marca se escribía al final, con el manejador entero en la ventana:
    # dos entregas simultáneas del mismo evento —Stripe advierte de que las
    # hace— pasaban las dos. Ahora la cerradura es el PRIMARY KEY.
    if not await _billing.claim_event(event_id, event_type, event_dict):
        return {"received": True}

    if manejado:
        try:
            await _handle_subscription_event(event["data"]["object"], event_type)
        except Exception:
            # Soltar la reserva es lo que mantiene útil el reintento de Stripe.
            # Sin esto, un fallo transitorio de la base de datos convertiría el
            # evento en pérdida definitiva: volvería y le diríamos «ya
            # procesado».
            await _billing.discard_event(event_id)
            raise
    return {"received": True}

def _amount_from_event(sub: Dict[str, Any]) -> int | None:
    """Importe recurrente según Stripe: `unit_amount` × `quantity` de cada item.

    De todo lo que guarda la fila, el importe era el único campo que no salía
    del evento: se copiaba de `existing` o se ponía a cero. O sea que el número
    venía de nuestra aritmética, que es justo la fuente que este webhook existe
    para no tener que creer — y un cambio hecho desde el panel de Stripe, un
    cupón o un ajuste manual dejaban la fila diciendo tres asientos con el
    precio de dos.

    Es el neto sin impuesto, que es lo que la columna ha guardado siempre: el
    alta escribe `totals["amount_cents"]`, y el desglose con impuesto de
    `_invoice_totals` solo viaja en la respuesta del checkout.

    `None` cuando el evento no trae ningún precio, para poder distinguirlo de un
    importe que de verdad es cero.
    """
    items = _safe_get(_safe_get(sub, "items", {}), "data", []) or []
    total = 0
    encontrado = False
    for item in items:
        unitario = _safe_get(_safe_get(item, "price") or {}, "unit_amount")
        if unitario is None:
            continue
        total += int(unitario) * int(_safe_get(item, "quantity", 1) or 1)
        encontrado = True
    return total if encontrado else None


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
        # No debería ocurrir en flujo normal: el checkout escribe user_id en la
        # metadata. Cuando ocurre —alta hecha desde el panel de Stripe, un
        # stripe_customer_id sin enlazar— hay un cobro que no se convierte en
        # plan, y antes eso no dejaba ni una línea: el payload quedaba en
        # stripe_events, tabla que no consulta ningún endpoint.
        #
        # Se responde 200 a propósito. Un 5xx haría que Stripe reintentase tres
        # días, lo que solo sirve si la causa es transitoria, y aquí casi nunca
        # lo es; a cambio, los fallos acumulados pueden acabar con el endpoint
        # deshabilitado, y entonces se pierden también las renovaciones y las
        # bajas. El reproceso es el «Resend» del panel de Stripe, y esta línea
        # es la que avisa de que hay algo que reprocesar.
        flog.audit(
            "billing.webhook.unattributed",
            resource_type="stripe_subscription",
            resource_id=str(_safe_get(sub, "id") or "-"),
            outcome="FAILURE",
            details={
                "event_type": event_type,
                "stripe_customer_id": str(_safe_get(sub, "customer") or "-"),
            },
            summary="Suscripción de Stripe sin usuario al que atribuirla",
        )
        return

    tier = _safe_get(metadata, "tier") or "developer"
    try:
        seats = int(_safe_get(metadata, "seats", 1))
    except (TypeError, ValueError):
        seats = 1
    interval = _safe_get(metadata, "interval") or "month"
    self_hosted = _safe_get(metadata, "self_hosted") == "1"

    existing = await _billing.get_by_stripe_subscription_id(sub["id"])

    status = (
        "canceled" if event_type == "customer.subscription.deleted" else sub["status"]
    )

    # ponytail: una cancelación es terminal —Stripe nunca reactiva una
    # suscripción cancelada, abre otra con id nuevo—, así que un `updated`
    # antiguo reentregado después ya no puede resucitarla. No ordena dos
    # `updated` entre sí; para eso haría falta guardar el `created` del evento
    # en la fila.
    if existing and existing["status"] == "canceled" and status != "canceled":
        flog.warning(
            f"[billing] {event_type} descartado para {sub['id']}: la suscripción "
            f"ya estaba cancelada y Stripe no las reactiva"
        )
        return

    amount_cents = _amount_from_event(sub)
    if amount_cents is None:
        # El respaldo era el camino principal, y por eso nadie había visto el
        # problema: un cero escrito una vez lo copiaban todos los `updated`
        # siguientes y no tenía ningún camino de vuelta.
        amount_cents = existing["amount_cents"] if existing else 0
        flog.warning(
            f"[billing] {event_type} de {sub['id']} sin precio en los items; "
            f"se conserva {amount_cents}"
        )
    if amount_cents == 0 and status not in ("canceled", "incomplete_expired"):
        # Un 0 en una fila activa es una incoherencia, no un dato: es el precio
        # que la aplicación le enseña a alguien a quien Stripe sí está cobrando.
        flog.warning(
            f"[billing] suscripción {sub['id']} en estado {status} con importe 0"
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

    # La traza que se echa de menos justo cuando hay una discusión sobre un
    # cargo: qué evento llegó, a quién se aplicó y en qué estado quedó. Solo la
    # forma del plan, que el propio usuario ya recibe por GET
    # /api/billing/subscription; ningún importe ni dato de pago entra en el log
    # central, que tiene su propia retención.
    flog.audit(
        "billing.subscription.sync",
        resource_type="stripe_subscription",
        resource_id=sub["id"],
        outcome="SUCCESS",
        username=user_id,
        details={
            "event_type": event_type,
            "tier": tier,
            "seats": seats,
            "status": status,
        },
        summary=f"Suscripción de Stripe aplicada: {status}",
    )
