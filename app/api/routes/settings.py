"""Rutas de configuración por usuario (tema, idioma, layout de dashboard)."""
from __future__ import annotations

import json

from app.utils import flog
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.auth import require_auth
from app.config.data import DB_FILE
from app.storage.db import close_db, open_db, PH, run_db

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PUBLIC_KEYS = {"theme", "language"}
_DEFAULTS = {"theme": "dark-red", "language": "es"}
_KNOWN_WIDGETS = {"summary", "token-usage", "activity", "conn-status", "recent"}

VALID_THEMES = {
    "dark-red", "dark-blue", "dark-orange", "dark-purple",
    "light-red", "light-blue", "light-orange", "light-purple",
    # legacy names kept for backward compatibility
    "noir", "marble", "ember", "ocean", "forest", "dusk",
}
VALID_LANGUAGES = {"es", "en"}


def _get_prefs(username: str) -> dict:
    db = open_db(DB_FILE)
    try:
        row = db.execute(
            f"SELECT preferences FROM users WHERE username = {PH}", (username,)
        ).fetchone()
    finally:
        close_db(db)
    if not row or not row["preferences"]:
        return {}
    try:
        return json.loads(row["preferences"])
    except Exception as exc:
        flog.warning(f"[settings] Preferencias corruptas para {username}: {exc}")
        return {}


def _save_prefs(username: str, prefs: dict) -> None:
    db = open_db(DB_FILE)
    try:
        db.execute(
            f"UPDATE users SET preferences = {PH} WHERE username = {PH}",
            (json.dumps(prefs), username),
        )
        db.commit()
    finally:
        close_db(db)


class SettingsUpdate(BaseModel):
    theme: Optional[str] = None
    language: Optional[str] = None


class DashboardLayoutUpdate(BaseModel):
    layout: List[str]


class DashboardConfigUpdate(BaseModel):
    config: dict


@router.get("")
async def get_settings(username: str = Depends(require_auth)) -> dict:
    prefs = await run_db(lambda: _get_prefs(username))
    return {k: prefs.get(k, _DEFAULTS[k]) for k in _PUBLIC_KEYS}


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    username: str = Depends(require_auth),
) -> dict:
    prefs = await run_db(lambda: _get_prefs(username))
    if body.theme is not None:
        if body.theme not in VALID_THEMES:
            raise HTTPException(status_code=422, detail=f"Tema no válido: {body.theme}")
        prefs["theme"] = body.theme
    if body.language is not None:
        if body.language not in VALID_LANGUAGES:
            raise HTTPException(status_code=422, detail=f"Idioma no válido: {body.language}")
        prefs["language"] = body.language
    await run_db(lambda: _save_prefs(username, prefs))
    return {k: prefs.get(k, _DEFAULTS[k]) for k in _PUBLIC_KEYS}


@router.get("/dashboard-layout")
async def get_dashboard_layout(username: str = Depends(require_auth)) -> dict:
    prefs = await run_db(lambda: _get_prefs(username))
    return {"layout": prefs.get("dashboard_layout", None)}


@router.put("/dashboard-layout")
async def update_dashboard_layout(
    body: DashboardLayoutUpdate,
    username: str = Depends(require_auth),
) -> dict:
    unknown = [w for w in body.layout if w not in _KNOWN_WIDGETS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Widgets desconocidos: {unknown}")
    prefs = await run_db(lambda: _get_prefs(username))
    prefs["dashboard_layout"] = body.layout
    await run_db(lambda: _save_prefs(username, prefs))
    return {"layout": body.layout}


@router.get("/dashboard-config")
async def get_dashboard_config(username: str = Depends(require_auth)) -> dict:
    prefs = await run_db(lambda: _get_prefs(username))
    return {"config": prefs.get("dashboard_config", {})}


@router.put("/dashboard-config")
async def update_dashboard_config(
    body: DashboardConfigUpdate,
    username: str = Depends(require_auth),
) -> dict:
    prefs = await run_db(lambda: _get_prefs(username))
    prefs["dashboard_config"] = body.config
    await run_db(lambda: _save_prefs(username, prefs))
    return {"config": body.config}
