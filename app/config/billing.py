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

# ── Impuestos ─────────────────────────────────────────────────────────────────
# Hasta ahora se cobraba el importe neto y nadie repercutía IVA: vendiendo
# servicios digitales a consumidores de la UE el tipo aplicable es el del país
# del comprador (ventanilla única), y a empresas de otro Estado miembro con
# NIF-IVA válido se les factura con inversión del sujeto pasivo. Ninguna de las
# dos cosas se puede improvisar en el momento del cobro, así que la calcula
# Stripe Tax a partir del país que el cliente declara antes de pagar.
#
# Sale activado a propósito: no cobrar IVA es un problema silencioso —la
# suscripción funciona y el descuadre aparece en la declaración—, mientras que
# tenerlo activo sin configurar Stripe Tax falla en el checkout, ruidosamente y
# con un mensaje que dice qué hacer. Una instalación que no lo quiera lo apaga
# con STRIPE_TAX=false.
#
# Requiere, en el panel de Stripe: Tax activado, las obligaciones fiscales
# («registrations») del país declaradas, un `tax_code` en el producto de
# asientos y `tax_behavior` en el precio del add-on self-hosted, que es fijo y
# no se crea desde aquí.
STRIPE_TAX_ENABLED: bool = os.getenv("STRIPE_TAX", "true").lower() not in (
    "0",
    "false",
    "no",
)

# Los precios anunciados son sin impuestos y el IVA se suma encima. La
# alternativa ("inclusive") haría que 9 € fuesen 9 € finales y el ingreso neto
# variase con el país del comprador, que no es el modelo con el que están
# calculados los precios. La página pública tiene que decir «IVA no incluido».
STRIPE_TAX_BEHAVIOR: str = "exclusive"


def is_configured() -> bool:
    return bool(STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET and STRIPE_PRODUCT_SEATS)
