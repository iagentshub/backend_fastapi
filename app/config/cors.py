"""Configuración de CORS."""
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)

# En producción, GAIA_FRONTEND_URL (o GAIA_CORS_ORIGINS para múltiples orígenes)
# es obligatoria. Sin ella el servidor arranca en modo desarrollo con orígenes
# localhost únicamente — NUNCA con wildcard "*" porque FastAPI/Starlette lo
# convierte en "refleja el Origin del request" cuando allow_credentials=True,
# lo que permite peticiones autenticadas desde cualquier dominio externo.

_raw = os.getenv("GAIA_CORS_ORIGINS") or os.getenv("GAIA_FRONTEND_URL", "")

if _raw:
    CORS_ORIGINS: list[str] = [o.strip() for o in _raw.split(",") if o.strip()]
else:
    # Sin configuración explícita: sólo orígenes locales (modo desarrollo)
    CORS_ORIGINS = [
        "http://localhost",
        "http://localhost:8007",
        "http://127.0.0.1",
        "http://127.0.0.1:8007",
    ]
    _log.warning(
        "CORS: GAIA_FRONTEND_URL no está configurada — "
        "permitiendo únicamente orígenes localhost. "
        "Define GAIA_FRONTEND_URL en producción."
    )
