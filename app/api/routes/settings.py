"""Rutas de configuración por usuario (tema, idioma, layout de dashboard)."""

from __future__ import annotations

import json

from app.utils import flog
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.auth import require_auth, require_admin
from app.storage.db import open_db

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PUBLIC_KEYS = {"theme", "language"}
_DEFAULTS = {"theme": "dark-red", "language": "es"}
_KNOWN_WIDGETS = {"summary", "token-usage", "activity", "conn-status", "recent"}

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


async def _get_prefs(username: str) -> dict:
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT preferences FROM users WHERE username = ?", (username,)
        )
    if not row or not row["preferences"]:
        return {}
    try:
        return json.loads(row["preferences"])
    except Exception as exc:
        flog.warning(f"[settings] Preferencias corruptas para {username}: {exc}")
        return {}


async def _save_prefs(username: str, prefs: dict) -> None:
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET preferences = ? WHERE username = ?",
            (json.dumps(prefs), username),
        )
        await conn.commit()


class SettingsUpdate(BaseModel):
    theme: Optional[str] = None
    language: Optional[str] = None


class DashboardLayoutUpdate(BaseModel):
    layout: List[str]


class DashboardConfigUpdate(BaseModel):
    config: dict


@router.get("")
async def get_settings(username: str = Depends(require_auth)) -> dict:
    prefs = await _get_prefs(username)
    return {k: prefs.get(k, _DEFAULTS[k]) for k in _PUBLIC_KEYS}


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    username: str = Depends(require_auth),
) -> dict:
    prefs = await _get_prefs(username)
    if body.theme is not None:
        if body.theme not in VALID_THEMES:
            raise HTTPException(status_code=422, detail=f"Tema no válido: {body.theme}")
        prefs["theme"] = body.theme
    if body.language is not None:
        if body.language not in VALID_LANGUAGES:
            raise HTTPException(
                status_code=422, detail=f"Idioma no válido: {body.language}"
            )
        prefs["language"] = body.language
    await _save_prefs(username, prefs)
    return {k: prefs.get(k, _DEFAULTS[k]) for k in _PUBLIC_KEYS}


@router.get("/dashboard-layout")
async def get_dashboard_layout(username: str = Depends(require_auth)) -> dict:
    prefs = await _get_prefs(username)
    return {"layout": prefs.get("dashboard_layout", None)}


@router.put("/dashboard-layout")
async def update_dashboard_layout(
    body: DashboardLayoutUpdate,
    username: str = Depends(require_auth),
) -> dict:
    unknown = [w for w in body.layout if w not in _KNOWN_WIDGETS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Widgets desconocidos: {unknown}")
    prefs = await _get_prefs(username)
    prefs["dashboard_layout"] = body.layout
    await _save_prefs(username, prefs)
    return {"layout": body.layout}


@router.get("/dashboard-config")
async def get_dashboard_config(username: str = Depends(require_auth)) -> dict:
    prefs = await _get_prefs(username)
    return {"config": prefs.get("dashboard_config", {})}


@router.put("/dashboard-config")
async def update_dashboard_config(
    body: DashboardConfigUpdate,
    username: str = Depends(require_auth),
) -> dict:
    prefs = await _get_prefs(username)
    prefs["dashboard_config"] = body.config
    await _save_prefs(username, prefs)
    return {"config": body.config}


class AdminSettingsUpdate(BaseModel):
    log_retention_days: Optional[int] = None


@router.get("/admin")
async def get_admin_settings(username: str = Depends(require_admin)) -> dict:
    """Devuelve la configuración exclusiva de admin (p.ej. retención de logs)."""
    prefs = await _get_prefs(username)
    return {"log_retention_days": int(prefs.get("log_retention_days", 30))}


@router.put("/admin")
async def update_admin_settings(
    body: AdminSettingsUpdate,
    username: str = Depends(require_admin),
) -> dict:
    """Actualiza la configuración exclusiva de admin."""
    prefs = await _get_prefs(username)
    if body.log_retention_days is not None:
        if not (1 <= body.log_retention_days <= 365):
            raise HTTPException(
                status_code=422, detail="log_retention_days debe estar entre 1 y 365"
            )
        prefs["log_retention_days"] = body.log_retention_days
    await _save_prefs(username, prefs)
    return {"log_retention_days": int(prefs.get("log_retention_days", 30))}


# ── Configuración de plataforma (settings.json) ───────────────────────────────

_PLATFORM_DEFAULTS: dict = {
    "billing_enabled": False,
    "registration": "open",  # open | closed | invite
    "max_users": 0,  # 0 = sin límite
    "max_concurrent_sessions": 0,  # 0 = sin límite
    "guest_enabled": True,
    "email_verify": False,
    "log_retention_days": 30,
}

_VALID_REGISTRATION = {"open", "closed"}


def _read_platform_cfg() -> dict:
    from app.config.data import SETTINGS_FILE
    import json as _json

    try:
        raw = _json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    cfg = dict(_PLATFORM_DEFAULTS)
    for k in _PLATFORM_DEFAULTS:
        if k in raw:
            cfg[k] = raw[k]
    return cfg


def _write_platform_cfg(cfg: dict) -> None:
    from app.config.data import SETTINGS_FILE
    import json as _json

    try:
        existing = _json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        existing = {}
    existing.update(cfg)
    SETTINGS_FILE.write_text(
        _json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
    )


class PlatformConfigUpdate(BaseModel):
    billing_enabled: Optional[bool] = None
    registration: Optional[str] = None
    max_users: Optional[int] = None
    max_concurrent_sessions: Optional[int] = None
    guest_enabled: Optional[bool] = None
    email_verify: Optional[bool] = None
    log_retention_days: Optional[int] = None


@router.get("/platform/public")
async def get_platform_config_public() -> dict:
    """Devuelve solo los campos públicos de la config de plataforma (sin autenticación).
    Usado por login, registro y otras páginas públicas para adaptar el UI."""
    cfg = _read_platform_cfg()
    return {
        "billing_enabled": cfg.get("billing_enabled", False),
        "guest_enabled": cfg.get("guest_enabled", True),
        "registration": cfg.get("registration", "open"),
    }


@router.get("/platform")
async def get_platform_config(_: str = Depends(require_admin)) -> dict:
    """Devuelve la configuración global de la plataforma (solo admin)."""
    return _read_platform_cfg()


@router.put("/platform")
async def update_platform_config(
    body: PlatformConfigUpdate,
    _: str = Depends(require_admin),
) -> dict:
    """Actualiza la configuración global de la plataforma (escribe en settings.json)."""
    cfg = _read_platform_cfg()
    update = body.model_dump(exclude_none=True)

    if "registration" in update and update["registration"] not in _VALID_REGISTRATION:
        raise HTTPException(
            status_code=422, detail="registration debe ser 'open' o 'closed'"
        )
    if "max_users" in update and update["max_users"] < 0:
        raise HTTPException(status_code=422, detail="max_users debe ser >= 0")
    if "max_concurrent_sessions" in update and update["max_concurrent_sessions"] < 0:
        raise HTTPException(
            status_code=422, detail="max_concurrent_sessions debe ser >= 0"
        )
    if "log_retention_days" in update and not (
        1 <= update["log_retention_days"] <= 365
    ):
        raise HTTPException(
            status_code=422, detail="log_retention_days debe estar entre 1 y 365"
        )

    cfg.update(update)
    _write_platform_cfg(cfg)
    return cfg
