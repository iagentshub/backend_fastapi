"""Explorar: catálogo público, packs oficiales y perfil social.

Partido en paquete porque el módulo único llegó a 826 líneas con tres dominios
que no comparten casi nada: descubrir contenido, el catálogo oficial y el
perfil de un usuario (follow y feed).

    _router.py        `router` compartido, sin lógica.
    _shared.py        validación de `relation` y resolución de propietarios.
    catalog.py        listado, vista previa y relaciones de un recurso.
    official_packs.py listado, detalle y enlazado de packs oficiales.
    profile.py        recursos de un usuario, follow y feed.

Extraído en su día de `social.py`, que mezclaba visibilidad, exploración,
follow y link/fork en un solo fichero.
"""

from __future__ import annotations

from app.api.routes.explore._router import router

from . import (  # noqa: F401 — registran rutas en `router` al importarse
    catalog,
    official_packs,
    profile,
)

__all__ = ["router"]
