"""Rutas de configuración global (tema, idioma)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.routes.auth import require_auth
from app.config.data import SETTINGS_FILE

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PUBLIC_KEYS = {"theme", "language"}

VALID_THEMES = {"noir", "marble", "ember", "ocean", "forest", "dusk"}
VALID_LANGUAGES = {"es", "en"}


def _load() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class SettingsUpdate(BaseModel):
    theme: Optional[str] = None
    language: Optional[str] = None


@router.get("")
async def get_settings() -> dict:
    raw = _load()
    return {k: raw.get(k) for k in _PUBLIC_KEYS}


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    _: str = Depends(require_auth),
) -> dict:
    raw = _load()
    if body.theme is not None:
        if body.theme not in VALID_THEMES:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=f"Tema no válido: {body.theme}")
        raw["theme"] = body.theme
    if body.language is not None:
        if body.language not in VALID_LANGUAGES:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=f"Idioma no válido: {body.language}")
        raw["language"] = body.language
    _save(raw)
    return {k: raw.get(k) for k in _PUBLIC_KEYS}
