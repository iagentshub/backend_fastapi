"""Configuración del servidor HTTP."""
from __future__ import annotations

import os

HOST   = os.getenv("GAIA_HOST", "0.0.0.0")
PORT   = int(os.getenv("GAIA_PORT", "8765"))
RELOAD = os.getenv("GAIA_RELOAD", "true").lower() == "true"


def _workers() -> int:
    """Procesos uvicorn. Con reload uvicorn ignora el parámetro y usa uno solo.

    La usan main.py (para arrancarlos) y RateLimiter (para repartir su cuota
    entre ellos: cada worker es un proceso con su propio contador en memoria).
    """
    if RELOAD:
        return 1
    try:
        return max(1, int(os.getenv("GAIA_WORKERS", "4")))
    except ValueError:
        return 4


WORKERS = _workers()
