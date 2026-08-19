"""Conocimiento — items sueltos y packs.

Partido en paquete porque el módulo único llegó a 1634 líneas: el dominio de
packs (subida directa, subida por sesión y sincronización por manifiesto) había
crecido hasta ocupar dos tercios del fichero pegado al de items, que no
comparte con él más que los almacenes y la clasificación de ficheros.

    _router.py       `router` compartido, sin lógica.
    _shared.py       almacenes, labels, límites y clasificación de ficheros.
    items.py         texto, URL y documento suelto.
    packs.py         alta directa, consulta, edición y borrado de packs.
    pack_sessions.py subida por sesión, fichero a fichero.
    pack_sync.py     comparación contra el manifiesto del cliente.

El orden de estos imports lo decide isort, no nosotros, y aquí da igual: un
parámetro de path no cruza `/`, así que `PUT /{item_id}` casa con un único
segmento y nunca con `/packs/{pack_id}`. Lo que sí ensombrecería una ruta
literal es un parámetro del mismo número de segmentos registrado antes —
`tests/api/test_rutas_ensombrecidas.py` es lo que vigila que no aparezca.
"""

from __future__ import annotations

from app.api.routes.knowledge._router import router

from . import (  # noqa: F401 — registran rutas en `router` al importarse
    items,
    pack_sessions,
    pack_sync,
    packs,
)

__all__ = ["router"]
