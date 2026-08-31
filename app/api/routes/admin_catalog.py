"""Listado paginado de conexiones para el panel (`/api/v2/admin`).

Los `GET` de `/api/admin` devolvían la tabla entera: es el único sitio del
producto donde lo que sale no lo acota lo que tiene un usuario, sino lo que
tiene la instalación. Se retiraron los once.

Aquí solo sobrevive el de conexiones, que es el único con consumidor: el
selector de conexiones LLM de la importación oficial, que necesita el catálogo
completo y lo recorre con el colector cursor. El inventario del panel se pide
por `/api/v2/admin/explore`, que cubre los once tipos con columnas
normalizadas; publicar además un listado por tipo que nadie llama era
superficie de API que mantener sin nadie a quien servir.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.routes.auth import require_admin
from app.pagination.api import CursorPageResponse
from app.pagination.http import cursor_error
from app.pagination.models import CursorParams
from app.pagination.query import scoped_cursor_params
from app.pagination.total import ExactTotalTimeout
from app.services.admin_listings import connections_spec
from app.services.admin_resource_cursor_listing import list_admin_resource_cursor

router = APIRouter(prefix="/api/v2/admin", tags=["admin-v2"])


@router.get("/connections")
async def admin_list_connections_v2(
    q: str | None = Query(None, max_length=500),
    owner: str | None = Query(None, max_length=200),
    page: CursorParams = Depends(scoped_cursor_params),
    _: str = Depends(require_admin),
) -> CursorPageResponse[dict[str, Any]]:
    spec = connections_spec()
    try:
        result = await list_admin_resource_cursor(spec, page=page, query=q, owner=owner)
    except (ExactTotalTimeout, ValueError) as exc:
        raise cursor_error(exc, resource=spec.resource) from exc
    return CursorPageResponse.from_result(result, limit=page.limit)
