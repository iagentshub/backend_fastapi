"""Preferencias de usuario en crudo, que leen y escriben los cuatro submódulos."""


from __future__ import annotations

import json

from app.services.platform_settings import _read_platform_cfg
from app.sql import sql
from app.storage.db import open_db
from app.utils import flog

_DEFAULTS = {"theme": "dark-red", "language": "es"}

VALID_THEMES = {
    "dark-red",
    "dark-blue",
    "dark-orange",
    "dark-purple",
    "light-red",
    "light-blue",
    "light-orange",
    "light-purple",
    # legacy names kept for backward compatibility
    "noir",
    "marble",
    "ember",
    "ocean",
    "forest",
    "dusk",
}

VALID_LANGUAGES = {"es", "en"}

def _theme_policy() -> tuple[bool, str]:
    cfg = _read_platform_cfg()
    default_theme = str(cfg.get("default_theme") or _DEFAULTS["theme"])
    if default_theme not in VALID_THEMES:
        default_theme = _DEFAULTS["theme"]
    return bool(cfg.get("users_can_configure_theme", True)), default_theme

def _settings_response(prefs: dict) -> dict:
    configurable, default_theme = _theme_policy()
    preferred_theme = str(prefs.get("theme") or "")
    effective_theme = (
        preferred_theme
        if configurable and preferred_theme in VALID_THEMES
        else default_theme
    )
    return {
        "theme": effective_theme,
        "language": prefs.get("language", _DEFAULTS["language"]),
        "theme_configurable": configurable,
        "default_theme": default_theme,
        # Activados de fábrica: una cuenta nueva tiene que enterarse de que la
        # han invitado a un grupo sin haber configurado nada antes.
        "notify_email": prefs.get("notify_email", True) is not False,
        "notify_push": prefs.get("notify_push", True) is not False,
        # El catálogo lo publica el servidor y el cliente pinta lo que reciba:
        # una copia en el cliente se desincroniza en cuanto se añade un evento.
        "notification_categories": _categorias_de(prefs),
    }


def _categorias_de(prefs: dict) -> dict:
    """Estado de cada categoría, rellenando lo que el usuario nunca tocó."""
    from app.models.notification_kinds import categorias_publicas

    guardado = prefs.get("notifications")
    if not isinstance(guardado, dict):
        guardado = {}
    salida = {}
    for categoria in categorias_publicas():
        actual = guardado.get(categoria)
        if not isinstance(actual, dict):
            actual = {}
        salida[categoria] = {
            "email": actual.get("email", True) is not False,
            "push": actual.get("push", True) is not False,
        }
    return salida

async def _get_prefs(user_id: str) -> dict:
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/settings:preferences_of_user"), (user_id,)
        )
    if not row or not row["preferences"]:
        return {}
    try:
        return json.loads(row["preferences"])
    except (json.JSONDecodeError, TypeError) as exc:
        # La columna no es JSON válido. Ya se registra con el usuario afectado,
        # y caer a {} solo le devuelve las preferencias por defecto.
        flog.warning(f"[settings] Preferencias corruptas para {user_id}: {exc}")
        return {}

async def _save_prefs(user_id: str, prefs: dict) -> None:
    async with open_db() as conn:
        await conn.execute(
            sql("queries/settings:set_preferences"),
            (json.dumps(prefs), user_id),
        )
        await conn.commit()
