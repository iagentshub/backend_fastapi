"""Sanitización común de errores devueltos por proveedores.

URL, payload, catálogo y protocolo pertenecen al fichero de cada proveedor en
``app.connections``.
"""

from __future__ import annotations

import json


def _detalle_publico(body: str) -> str:
    """Extrae mensajes de negocio; nunca reenvía cuerpos arbitrarios."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return "sin detalle utilizable en la respuesta."
    if not isinstance(parsed, dict):
        return "sin detalle utilizable en la respuesta."
    error = parsed.get("error") or {}
    detail = (
        parsed.get("detail")
        or parsed.get("message")
        or (error.get("message") if isinstance(error, dict) else error)
    )
    if not isinstance(detail, str) or not detail.strip():
        return "sin detalle utilizable en la respuesta."
    return detail.strip()[:500]
