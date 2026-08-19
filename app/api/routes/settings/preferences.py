"""Ajustes del usuario: tema e idioma."""


from __future__ import annotations

from typing import Optional

from fastapi import Depends
from pydantic import BaseModel

from app.api.routes.auth import require_auth
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

@router.get("")
async def get_settings(username: str = Depends(require_auth)) -> dict:
    prefs = await _get_prefs(username)
    return _settings_response(prefs)

@router.put("")
async def update_settings(
    body: SettingsUpdate,
    username: str = Depends(require_auth),
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
    await _save_prefs(username, prefs)
    return _settings_response(prefs)
