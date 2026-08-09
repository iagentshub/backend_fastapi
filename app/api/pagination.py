"""Paginación de listados: recorte de página y total en cabecera.

Diez listados de la API declaraban `limit` y `offset` y devolvían una lista
pelada, así que el cliente podía pedir la página 3 pero no sabía cuántas hay ni
cuántos elementos existen. En pantalla eso es el patrón «cargar más» a ciegas:
sin barra de progreso, sin saltar a la última página y sin poder decir
«41-60 de 431». `/api/admin/logs` ya devuelve el sobre completo
(`{items, total, page, page_size, pages}`), pero es el único.

Cambiar el cuerpo de `[...]` a `{items: [...]}` rompería a la vez a todos los
clientes —el real es la app Flutter—, así que el total viaja en una cabecera:
es aditivo y no rompe nada. `X-Total-Count` se expone en CORS desde
`app/api/app.py`; sin ese `Access-Control-Expose-Headers` el navegador no deja
leerla en peticiones cross-origin y el total llegaría siempre vacío.
"""

from __future__ import annotations

from typing import List, Optional, TypeVar

from fastapi import Response

TOTAL_HEADER = "X-Total-Count"

T = TypeVar("T")


def paginar(
    items: List[T],
    limit: int,
    offset: int,
    response: Optional[Response] = None,
) -> List[T]:
    """Devuelve la página pedida y publica el total en `X-Total-Count`.

    El total es el de ANTES de recortar: es lo que el cliente necesita para
    pintar el paginador. `response` es opcional para que la función siga siendo
    usable desde código que no tiene una respuesta a mano.
    """
    if response is not None:
        response.headers[TOTAL_HEADER] = str(len(items))
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    return items
