"""Facturación: suscripción, licencias y webhook de Stripe.

Partido en paquete porque el módulo único llegó a 621 líneas mezclando tres
cosas con ciclos de vida distintos: lo que hace el titular de la cuenta, el
reparto de licencias entre sus usuarios y lo que Stripe nos cuenta por su lado.

    _shared.py      router, almacén, limitadores y configuración de stripe.
    subscription.py alta, cambio, baja y estado.
    licenses.py     asignar y revocar licencias.
    webhook.py      eventos de Stripe.
"""

from __future__ import annotations

from app.api.routes.billing._shared import router

from . import (  # noqa: F401 — registran rutas en `router` al importarse
    licenses,
    subscription,
    webhook,
)

__all__ = ["router"]
