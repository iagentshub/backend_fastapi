"""Utilidades de la petición HTTP: IP de cliente y cuerpo JSON."""
from __future__ import annotations

from typing import Any

from fastapi import Request

from app.config.session import TRUSTED_PROXIES
from app.errors import APIError


async def json_body(request: Request) -> dict[str, Any]:
    """Cuerpo JSON de la petición, garantizando que es un objeto.

    Los handlers hacían `body = await request.json()` y acto seguido
    `body.get(...)`. Mandar un array o un número —`[]` bastaba— reventaba con
    AttributeError y salía un 500 sin registrar nada útil, en endpoints que
    además son públicos, como el registro. El cuerpo viene de fuera: que no sea
    un objeto es entrada inválida (400), no un fallo del servidor.
    """
    try:
        body = await request.json()
    except ValueError:
        raise APIError(400, "invalid_json", "El cuerpo de la petición no es JSON válido")
    if not isinstance(body, dict):
        raise APIError(400, "invalid_json", "El cuerpo de la petición debe ser un objeto JSON")
    return body


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
