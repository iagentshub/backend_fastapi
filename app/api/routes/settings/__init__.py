"""Ajustes: preferencias del usuario, panel, plataforma y avisos.

Partido en paquete porque el módulo único llegó a 677 líneas con cuatro cosas
que solo comparten el prefijo de la URL: lo que elige un usuario para sí, cómo
coloca su panel, lo que decide el admin para toda la instalación y los avisos.

    _router.py     `router` compartido, sin lógica.
    _shared.py     preferencias en crudo, que leen y escriben los cuatro.
    preferences.py tema e idioma.
    dashboard.py   layout v1, layout v2 y widgets.
    platform.py    configuración de plataforma del admin.
    banners.py     avisos a toda la instalación.

Leer y escribir `settings.json` vive en `services/platform_settings.py`: lo
necesitan también `admin/updates.py`, `centinel/_state.py` y el middleware de
licencias, que antes importaban de dentro de la capa de rutas para llegar.
"""

from __future__ import annotations

from app.api.routes.settings._router import router

from . import (  # noqa: F401 — registran rutas en `router` al importarse
    banners,
    dashboard,
    platform,
    preferences,
)

__all__ = ["router"]
