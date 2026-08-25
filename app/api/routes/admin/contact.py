"""Lectura de las peticiones del formulario de contacto público.

Sin esto la tabla que escribe `/api/public/contact` no la lee nadie: el aviso
por correo se puede perder —SMTP mal configurado, un fallo de entrega— y la
copia guardada sería inalcanzable salvo abriendo la base de datos a mano.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Depends, Query

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.storage.contact import list_contact_requests


@admin_router.get("/contact-requests")
async def get_contact_requests(
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Las últimas peticiones recibidas, de la más reciente a la más antigua."""
    return await list_contact_requests(limit)
