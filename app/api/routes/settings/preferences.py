"""Ajustes del usuario: tema, idioma y canales de aviso."""


from __future__ import annotations

from typing import Dict, Optional

from fastapi import Depends
from pydantic import BaseModel

from app.api.routes.auth import require_session
from app.api.routes.settings._router import router
from app.api.routes.settings._shared import (
    VALID_LANGUAGES,
    VALID_THEMES,
    _get_prefs,
    _save_prefs,
    _settings_response,
    _theme_policy,
)
from app.errors import APIError


class SettingsUpdate(BaseModel):
    theme: Optional[str] = None
    language: Optional[str] = None
    # Canales de las notificaciones. La campana no se apaga: es el registro
    # de lo que ha pasado, no una interrupción, y sin ella el usuario se
    # quedaría sin forma de enterarse de nada.
    notify_email: Optional[bool] = None
    notify_push: Optional[bool] = None
    # `{categoria: {email: bool, push: bool}}`. Se fusiona con lo guardado,
    # no lo reemplaza: la pantalla manda solo el interruptor que se tocó.
    notification_categories: Optional[Dict[str, Dict[str, bool]]] = None

@router.get("")
async def get_settings(username: str = Depends(require_session)) -> dict:
    prefs = await _get_prefs(username)
    return _settings_response(prefs)

@router.put("")
async def update_settings(
    body: SettingsUpdate,
    username: str = Depends(require_session),
) -> dict:
    prefs = await _get_prefs(username)
    if body.theme is not None:
        configurable, _ = _theme_policy()
        if not configurable:
            raise APIError(
                403,
                "forbidden",
                "El administrador ha desactivado la personalización del tema",
                extra={"field": "theme"},
            )
        if body.theme not in VALID_THEMES:
            raise APIError(
                422,
                "invalid_field",
                f"Tema no válido: {body.theme}",
                extra={"field": "theme"},
            )
        prefs["theme"] = body.theme
    if body.language is not None:
        if body.language not in VALID_LANGUAGES:
            raise APIError(
                422,
                "invalid_field",
                f"Idioma no válido: {body.language}",
                extra={"field": "language"},
            )
        prefs["language"] = body.language
    if body.notify_email is not None:
        prefs["notify_email"] = body.notify_email
    if body.notify_push is not None:
        prefs["notify_push"] = body.notify_push
    if body.notification_categories is not None:
        prefs["notifications"] = _fusionar_categorias(
            prefs.get("notifications"), body.notification_categories
        )
    await _save_prefs(username, prefs)
    return _settings_response(prefs)


def _fusionar_categorias(guardado: object, entrantes: Dict[str, Dict[str, bool]]) -> dict:
    """Aplica solo lo que llega, dejando el resto como estaba.

    La pantalla manda el interruptor que el usuario acaba de mover, no la tabla
    entera: reemplazar el bloque completo apagaría lo que no viajó en el
    cuerpo. Las categorías desconocidas se descartan para que nadie engorde
    `users.preferences` con claves inventadas.
    """
    from app.models.notification_kinds import categorias_publicas

    validas = set(categorias_publicas())
    salida = dict(guardado) if isinstance(guardado, dict) else {}
    for categoria, canales in entrantes.items():
        if categoria not in validas or not isinstance(canales, dict):
            continue
        actual = salida.get(categoria)
        actual = dict(actual) if isinstance(actual, dict) else {}
        for canal in ("email", "push"):
            if canal in canales:
                actual[canal] = bool(canales[canal])
        salida[categoria] = actual
    return salida
