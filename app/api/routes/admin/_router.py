"""`admin_router` compartido — cada submódulo de `app.api.routes.admin` le
registra sus propias rutas. Vive en su propio fichero para que ningún
submódulo tenga que importar de otro submódulo para llegar al router."""

from __future__ import annotations

from fastapi import APIRouter

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])
