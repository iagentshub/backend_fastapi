"""Lo poco que comparten los tres módulos de sesión.

`_public_base_url` la usan el registro (enlace de verificación) y el olvido de
contraseña (enlace de reseteo): los dos mandan por correo una URL absoluta.
"""


from __future__ import annotations

import os

from fastapi import Request


def _public_base_url(request: Request) -> str:
    """URL base canónica para construir enlaces en emails.

    Usa GAIA_FRONTEND_URL si está configurada (evita Host Header Injection).
    En desarrollo usa un origen local fijo; nunca confía en la cabecera Host.
    """
    del request  # La firma sigue siendo cómoda para los handlers FastAPI.
    configured = os.getenv("GAIA_FRONTEND_URL", "").rstrip("/")
    if configured:
        return configured
    return "http://localhost:8007"
