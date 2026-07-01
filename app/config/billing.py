"""Configuración de Stripe — claves API y catálogo de productos."""
from __future__ import annotations

import os

STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Pin explícito — la API de Stripe ha movido campos (p.ej. current_period_end)
# entre el objeto Subscription y SubscriptionItem según versión.
STRIPE_API_VERSION: str = os.getenv("STRIPE_API_VERSION", "2024-06-20")

# Producto usado para precios inline por asiento (developer/business)
STRIPE_PRODUCT_SEATS: str = os.getenv("STRIPE_PRODUCT_SEATS", "")

# Precio fijo del add-on self-hosted (mensual/anual)
STRIPE_PRICE_SELFHOSTED_MONTHLY: str = os.getenv("STRIPE_PRICE_SELFHOSTED_MONTHLY", "")
STRIPE_PRICE_SELFHOSTED_ANNUAL: str = os.getenv("STRIPE_PRICE_SELFHOSTED_ANNUAL", "")


def is_configured() -> bool:
    return bool(STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET and STRIPE_PRODUCT_SEATS)
