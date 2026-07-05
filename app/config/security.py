"""Constantes de seguridad: rangos de red bloqueados y otras restricciones."""
from __future__ import annotations

from urllib.parse import urlparse

# Prefijos de hostname/IP bloqueados para prevenir SSRF hacia redes privadas,
# loopback y endpoints de metadata de cloud (AWS, GCP, Azure).
PRIVATE_HOST_PREFIXES: tuple[str, ...] = (
    "127.", "0.", "10.", "192.168.", "169.254.",  # loopback, privadas, link-local (RFC 1918)
    "172.16.", "172.17.", "172.18.", "172.19.",   # Docker y privadas RFC 1918
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "::1", "fc", "fd",                             # IPv6 loopback y ULA
    "localhost",                                   # hostname literal
)


def assert_safe_url(url: str) -> None:
    """Lanza ValueError si la URL apunta a una red privada o metadata de cloud.

    Úsalo antes de cualquier fetch externo iniciado por input del usuario
    para prevenir SSRF (Server-Side Request Forgery).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"URL inválida: {url!r}")

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Solo se permiten URLs http/https")

    host = (parsed.hostname or "").lower().strip("[]")  # strip IPv6 brackets
    if not host:
        raise ValueError("URL sin hostname")

    if any(host == p.rstrip(".") or host.startswith(p) for p in PRIVATE_HOST_PREFIXES):
        raise ValueError(
            "La URL apunta a una dirección de red privada o reservada, "
            "lo cual no está permitido por seguridad."
        )
