"""`router`, almacén, limitadores y helpers comunes a las tres partes.

La configuración global de `stripe` (clave y versión de API) vive aquí porque
es un efecto de importación que debe ocurrir una sola vez y antes de la primera
llamada, y los tres submódulos importan de este.

`_extract_period_end` la comparten la suscripción y el webhook: el fin de
periodo llega con la misma forma por los dos caminos.
"""


from __future__ import annotations

from typing import Any, Dict, Optional

import stripe
from fastapi import APIRouter, HTTPException

from app.config.billing import (
    STRIPE_API_VERSION,
    STRIPE_PRICE_SELFHOSTED_ANNUAL,
    STRIPE_PRICE_SELFHOSTED_MONTHLY,
    STRIPE_SECRET_KEY,
)
from app.config.session import RATE_IP_FACTOR
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter, principal_key
from app.storage.billing import BillingStorage
from app.utils.net import client_ip as _client_ip

stripe.api_key = STRIPE_SECRET_KEY
stripe.api_version = STRIPE_API_VERSION


router = APIRouter(prefix="/api/billing", tags=["billing"])

_billing = BillingStorage()

# /subscribe exige sesión: la cuota es de la cuenta que abre el checkout.
_subscribe_limiter = RateLimiter(
    calls=10,
    window=60,
    key_func=principal_key,
    shared=True,
    name="billing-subscribe",
    ip_calls=10 * RATE_IP_FACTOR,
)

# /quote es público —la página de precios la consulta sin sesión—, así que
# aquí la IP no es un mal sustituto: es la única identidad que hay.
_quote_limiter = RateLimiter(
    calls=30,
    window=60,
    key_func=_client_ip,
    shared=True,
    name="billing-quote",
)

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
