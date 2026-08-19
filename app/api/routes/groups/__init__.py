"""Grupos: el grupo en sí, sus miembros y sus invitaciones.

Partido en paquete porque el módulo único llegó a 604 líneas con tres ciclos de
vida distintos sobre el mismo prefijo.

    _shared.py      router, almacén y guardas comunes.
    crud.py         alta, edición, borrado, traspaso y cambio de grupo activo.
    members.py      alta, baja y rol de los miembros.
    invitations.py  invitaciones recibidas y emitidas.
"""

from __future__ import annotations

from app.api.routes.groups._shared import router

from . import (  # noqa: F401 — registran rutas en `router` al importarse
    crud,
    invitations,
    members,
)

__all__ = ["router"]
