"""Enlazar, sincronizar y probar recursos del catálogo público.

Partido en paquete porque el módulo único llegó a 779 líneas con tres verbos
distintos sobre el mismo material:

    _router.py  `router` compartido, sin lógica.
    _shared.py  los almacenes.
    link.py     copiar un recurso ajeno al espacio propio.
    sync.py     traer los cambios del original a la copia enlazada.
    trial.py    probar un agente público sin enlazarlo.

La herencia de dependencias al enlazar vive en
`services/resource_inheritance.py`; salió de `social.py` en el mismo cambio.
"""

from __future__ import annotations

from app.api.routes.resource_linking._router import router

from . import (  # noqa: F401 — registran rutas en `router` al importarse
    link,
    sync,
    trial,
)

__all__ = ["router"]
