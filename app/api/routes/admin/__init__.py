"""Admin routes — panel de administración: usuarios, recursos globales,
configuración de plataforma y auto-actualización.

Extraído de auth.py (que se estaba acercando a las 2000 líneas mezclando
sesión de usuario y administración) para que un fix de ownership/acceso en
un sitio no se quede sin replicar en el otro por simple tamaño del archivo.
Las dependencias de autenticación (`require_admin`, `require_auth`,
`GroupContext`, etc.) siguen viviendo en auth.py — son el contrato que
importan ~18 archivos de todo el backend y moverlas habría sido un cambio de
alto riesgo sin beneficio real.

BE-08: a su vez, admin.py (1786 líneas) se partió en este paquete por
responsabilidad — no persiguiendo ciclos, que no los había:
    _router.py   `admin_router` compartido, sin lógica.
    updates.py   versión GHCR/GitHub + Watchtower.
    stats.py     metadatos de tablas, salud del servidor, /stats.
    users.py     alta/edición/borrado de usuarios, impersonar.
    resources.py CRUD de conexiones, agentes, skills, prompts, memoria,
                 conocimiento, orquestaciones, grupos y reasignación de
                 propietario/verificación de recursos.
    explore.py   inventario unificado y grafo de relaciones — depende de
                 users.py y resources.py para reutilizar sus listados en
                 vez de duplicar las consultas.
"""

from __future__ import annotations

from app.api.routes.admin._router import admin_router

from . import (  # noqa: F401 — registran rutas en admin_router al importarse
    explore,
    official_packages,
    resources,
    stats,
    updates,
    users,
)

__all__ = ["admin_router"]
