"""Constantes de seguridad: rangos de red bloqueados y otras restricciones."""
from __future__ import annotations

import ipaddress
import socket
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

_SSRF_ERROR = (
    "La URL apunta a una dirección de red privada o reservada, "
    "lo cual no está permitido por seguridad."
)


def assert_safe_url(url: str) -> None:
    """Lanza ValueError si la URL apunta a una red privada o metadata de cloud.

    Úsalo antes de cualquier fetch externo iniciado por input del usuario
    para prevenir SSRF (Server-Side Request Forgery).

    Aplica dos capas de verificación:
    1. Prefijos de string (rápido, sin red) — cubre casos habituales.
    2. ipaddress — cubre IPv4-mapped IPv6 (::ffff:7f00:1, ::ffff:169.254.x.x)
       que el check de prefijos no detecta.
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

    # Check 1: prefix-based (rápido, sin red)
    if any(host == p.rstrip(".") or host.startswith(p) for p in PRIVATE_HOST_PREFIXES):
        raise ValueError(_SSRF_ERROR)

    # Check 2: ipaddress — detecta IPv4-mapped IPv6 y otras representaciones
    # que el check de prefijos no cubre (e.g. ::ffff:7f00:0001 = 127.0.0.1)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # El host es un nombre de dominio, no una IP literal — OK
        return

    is_dangerous = (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
    )
    # IPv4-mapped IPv6: ::ffff:169.254.169.254, ::ffff:127.0.0.1, etc.
    ipv4_mapped = getattr(addr, "ipv4_mapped", None)
    if ipv4_mapped:
        is_dangerous = is_dangerous or (
            ipv4_mapped.is_private
            or ipv4_mapped.is_loopback
            or ipv4_mapped.is_link_local
        )

    if is_dangerous:
        raise ValueError(_SSRF_ERROR)


def resolve_safe_host(host: str, port: int) -> str:
    """Resuelve ``host`` y devuelve una IP pública fijada para la conexión.

    Se validan *todas* las respuestas DNS: aceptar un conjunto mixto público/
    privado permitiría que otra resolución posterior eligiese la dirección
    privada. El llamador debe conectarse directamente a la IP devuelta para
    evitar una segunda resolución vulnerable a DNS rebinding.
    """
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("No se pudo resolver el hostname de la URL") from exc
    if not addresses:
        raise ValueError("El hostname de la URL no tiene direcciones")

    public_ips: list[str] = []
    for _family, _socktype, _proto, _canonname, sockaddr in addresses:
        raw_ip = str(sockaddr[0]).split("%", 1)[0]
        try:
            addr = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise ValueError("La resolución DNS devolvió una dirección inválida") from exc
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_unspecified
            or addr.is_multicast
        ):
            raise ValueError(_SSRF_ERROR)
        public_ips.append(raw_ip)
    return public_ips[0]
