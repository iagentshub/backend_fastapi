"""`router` compartido de conocimiento — cada submódulo le registra sus rutas.

Vive en su propio fichero para que ningún submódulo tenga que importar de otro
submódulo sólo para llegar al router (mismo patrón que
`app/api/routes/admin/_router.py`).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
