"""Constantes de seguridad: rangos de red bloqueados y otras restricciones."""
from __future__ import annotations

# Prefijos de hostname/IP bloqueados para prevenir SSRF hacia redes privadas,
# loopback y endpoints de metadata de cloud (AWS, GCP, Azure).
PRIVATE_HOST_PREFIXES: tuple[str, ...] = (
    "127.", "0.", "10.", "192.168.", "169.254.",  # loopback, privadas, link-local
    "::1", "fc", "fd",                             # IPv6 loopback y ULA
)
