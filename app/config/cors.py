"""Configuración de CORS."""
from __future__ import annotations

import os

# En producción, deriva de GAIA_FRONTEND_URL (una sola variable para todo).
# GAIA_CORS_ORIGINS tiene prioridad si se define (p.ej. múltiples orígenes separados por coma).
# Sin ninguna var → "*" (desarrollo local).
CORS_ORIGINS: list[str] = (
    os.getenv("GAIA_CORS_ORIGINS")
    or os.getenv("GAIA_FRONTEND_URL", "*")
).split(",")
