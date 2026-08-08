"""Centinel — Test Runner para administradores.

Permite lanzar la suite de pytest desde el panel de administración y
visualizar los resultados en tiempo real vía Server-Sent Events (SSE).

Endpoints:
  GET  /api/admin/centinel/status          Estado actual del runner
  GET  /api/admin/centinel/tree            Árbol de tests descubiertos
  POST /api/admin/centinel/run             Lanza un run (background task)
  DEL  /api/admin/centinel/run             Aborta el run en curso
  GET  /api/admin/centinel/history         Últimas 5 ejecuciones
  GET  /api/admin/centinel/stream/{run_id} SSE stream de un run
Partido en paquete (fase 1.4 del refactor) porque el archivo único llegó a
1468 líneas mezclando tres runners con estado global y SSE propios:

    _router.py  `router` compartido, sin lógica.
    _state.py   estado de proceso y snapshot compartido entre workers.
    _shared.py  guard, SSE, broadcast e historial comunes a los tres.
    run.py      runner de pytest.
    stress.py   test de carga.
    probe.py    búsqueda del punto de quiebre.
"""

from __future__ import annotations

from app.api.routes.centinel._router import router

from . import (  # noqa: F401 — registran rutas en `router` al importarse
    probe,
    run,
    stress,
)

__all__ = ["router"]
