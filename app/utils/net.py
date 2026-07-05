"""Utilidades de red: extracción segura de IP de cliente."""
from __future__ import annotations

from fastapi import Request

from app.config.session import TRUSTED_PROXIES


def client_ip(request: Request) -> str:
    """Devuelve la IP real del cliente de forma segura.

    Solo se lee X-Forwarded-For cuando la conexión TCP proviene de un proxy
    confiable (GAIA_TRUSTED_PROXIES). Si no, usar X-Forwarded-For permitiría
    que cualquier cliente inyecte una IP arbitraria en el header y bypass
    todos los rate limiters.
    """
    peer = request.client.host if request.client else ""

    if peer in TRUSTED_PROXIES:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            # El primer valor es el cliente original según la spec de XFF
            ip = fwd.split(",")[0].strip()
            if ip:
                return ip

    return peer or "unknown"
