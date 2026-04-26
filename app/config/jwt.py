"""Configuración de JWT."""
from __future__ import annotations

import os

JWT_SECRET_ENV   = "GAIA_AGENTS_SECRET"
JWT_ALGORITHM    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("GAIA_JWT_EXPIRE_HOURS", "12"))

# Valores placeholder que indican que el secreto no ha sido configurado.
JWT_UNSAFE_SECRETS: frozenset[str] = frozenset({
    "",
    "REEMPLAZAR_O_USAR_GAIA_AGENTS_SECRET",  # valor por defecto en data/settings.json
    "cambia_esto_en_produccion",              # valor por defecto en .env.example
})
