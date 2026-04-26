"""Configuración de sesión: tokens, expiración y rate limiting de login."""
from __future__ import annotations

import os

JWT_SECRET_ENV   = "GAIA_AGENTS_SECRET"
JWT_ALGORITHM    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("GAIA_JWT_EXPIRE_HOURS", "12"))

JWT_UNSAFE_SECRETS: frozenset[str] = frozenset({
    "",
    "REEMPLAZAR_O_USAR_GAIA_AGENTS_SECRET",
    "cambia_esto_en_produccion",
})

LOGIN_WINDOW    = int(os.getenv("GAIA_LOGIN_WINDOW",    "300"))   # segundos
LOGIN_MAX_FAILS = int(os.getenv("GAIA_LOGIN_MAX_FAILS", "5"))     # intentos fallidos

REGISTER_WINDOW = int(os.getenv("GAIA_REGISTER_WINDOW", "3600"))  # segundos
REGISTER_MAX    = int(os.getenv("GAIA_REGISTER_MAX",    "5"))     # registros por ventana
