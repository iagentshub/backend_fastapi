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


def request_origin(request: Request) -> str | None:
    """Origen al que iba dirigida la petición: «esquema://host[:puerto]».

    Es el segundo término contra el que se compara el `Origin` entrante, además
    de CORS_ORIGINS. No es redundante: en una petición cross-site el `Host` lo
    escribe el navegador apuntando a NUESTRO dominio —el atacante no lo
    controla—, así que aceptar el propio host es seguro y evita que un
    GAIA_CORS_ORIGINS desactualizado rechace tráfico legítimo, que es el modo
    de fallo difícil de diagnosticar desde el cliente.

    Mismo criterio de confianza que client_ip(): las cabeceras X-Forwarded-*
    solo se leen cuando la conexión TCP viene de un proxy declarado. Hace falta
    porque nginx manda `Host $proxy_host` —el host interno del contenedor— y
    pone el real en `X-Forwarded-Host`.
    """
    peer = request.client.host if request.client else ""
    host = ""
    scheme = ""

    if peer in TRUSTED_PROXIES:
        # El primer valor es el host original, igual que en XFF.
        host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        scheme = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()

    host = host or request.headers.get("host", "").strip()
    if not host:
        return None
    return f"{scheme or request.url.scheme}://{host}".lower()


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
