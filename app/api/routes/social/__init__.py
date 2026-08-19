"""Catálogo social: visibilidad pública y stars.

Partido en paquete porque el módulo único llegó a 1155 líneas de las que solo
un tercio eran rutas. Lo demás era lógica de negocio y acceso a datos que ahora
vive donde corresponde:

    services/publication_cascade.py    publicar arrastra las dependencias.
    services/resource_inheritance.py   enlazar clona lo que el recurso necesita.
    services/social_catalog.py         consultas y upsert de `resource_social`.
    services/resource_stores.py        los almacenes que comparten todos.

Aquí queda lo que es una ruta:

    _router.py    `router` y limitador compartidos, sin lógica.
    visibility.py publicar/despublicar cada tipo de recurso.
    stars.py      marcar y desmarcar.

Ver `explore.py` (descubrimiento/perfil/follow) y `resource_linking.py`
(link/fork/sync/try), extraídos antes de este mismo módulo.
"""

from __future__ import annotations

from app.api.routes.social._router import router

from . import (  # noqa: F401 — registran rutas en `router` al importarse
    stars,
    visibility,
)

__all__ = ["router"]
