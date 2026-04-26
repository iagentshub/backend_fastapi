"""Configuración de CORS."""
from __future__ import annotations

import os

# Para múltiples orígenes separar con coma: "http://localhost:3000,https://mi-app.com"
# Usar "*" solo en desarrollo local; en producción especificar el origen del frontend.
CORS_ORIGINS: list[str] = os.getenv("GAIA_CORS_ORIGINS", "*").split(",")
