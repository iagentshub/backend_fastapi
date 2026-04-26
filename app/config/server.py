"""Configuración del servidor HTTP."""
from __future__ import annotations

import os

HOST   = os.getenv("GAIA_HOST", "0.0.0.0")
PORT   = int(os.getenv("GAIA_PORT", "8765"))
RELOAD = os.getenv("GAIA_RELOAD", "true").lower() == "true"
