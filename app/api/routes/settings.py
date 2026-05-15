"""Rutas de configuración por usuario (tema, idioma)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.auth import require_auth
from app.config.data import DB_FILE
from app.storage.db import open_db, PH

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PUBLIC_KEYS = {"theme", "language"}
_DEFAULTS = {"theme": "noir", "language": "es"}

VALID_THEMES = {"noir", "marble", "ember", "ocean", "forest", "dusk"}
VALID_LANGUAGES = {"es", "en"}


def _get_prefs(username: str) -> dict:
    db = open_db(DB_FILE)
    row = db.execute(
        f"SELECT preferences FROM users WHERE username = {PH}", (username,)
    ).fetchone()
    if not row or not row["preferences"]:
        return {}
    try:
        return json.loads(row["preferences"])
    except Exception:
        return {}


def _save_prefs(username: str, prefs: dict) -> None:
    db = open_db(DB_FILE)
    db.execute(
        f"UPDATE users SET preferences = {PH} WHERE username = {PH}",
        (json.dumps(prefs), username),
    )
    db.commit()


class SettingsUpdate(BaseModel):
    theme: Optional[str] = None
    language: Optional[str] = None


@router.get("")
async def get_settings(username: str = Depends(require_auth)) -> dict:
    prefs = _get_prefs(username)
    return {k: prefs.get(k, _DEFAULTS[k]) for k in _PUBLIC_KEYS}


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    username: str = Depends(require_auth),
) -> dict:
    prefs = _get_prefs(username)
    if body.theme is not None:
        if body.theme not in VALID_THEMES:
            raise HTTPException(status_code=422, detail=f"Tema no válido: {body.theme}")
        prefs["theme"] = body.theme
    if body.language is not None:
        if body.language not in VALID_LANGUAGES:
            raise HTTPException(status_code=422, detail=f"Idioma no válido: {body.language}")
        prefs["language"] = body.language
    _save_prefs(username, prefs)
    return {k: prefs.get(k, _DEFAULTS[k]) for k in _PUBLIC_KEYS}
