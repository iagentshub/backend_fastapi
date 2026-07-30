"""Rutas de configuración por usuario (tema, idioma, layout de dashboard)."""

from __future__ import annotations

import json
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from app.api.routes.auth import require_admin, require_auth
from app.errors import APIError
from app.storage.db import open_db
from app.utils import flog

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PUBLIC_KEYS = {"theme", "language"}
_DEFAULTS = {"theme": "dark-red", "language": "es"}
_KNOWN_WIDGETS = {
    "summary",
    "token-usage",
    "activity",
    "conn-status",
    "recent",
    "recent-conversations",
    "composition",
    "feed",
    "quick-actions",
    "token-kpi",
    "recent-resources",
    "agent-health",
    "group",
}

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


class DashboardWidgetInstancePayload(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    type: str
    size: Literal["compact", "medium", "wide", "full"]
    config: dict[str, Any] = Field(default_factory=dict)


class DashboardLayoutV2Update(BaseModel):
    version: Literal[2] = 2
    items: list[DashboardWidgetInstancePayload] = Field(max_length=50)

    @model_validator(mode="after")
    def validate_items(self) -> "DashboardLayoutV2Update":
        unknown = sorted({item.type for item in self.items} - _KNOWN_WIDGETS)
        if unknown:
            raise ValueError(f"Widgets desconocidos: {unknown}")
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Los IDs de widget deben ser únicos")
        return self


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
        raise APIError(
            422,
            "invalid_field",
            f"Widgets desconocidos: {unknown}",
            extra={"field": "widgets"},
        )
    prefs = await _get_prefs(username)
    prefs["dashboard_layout"] = body.layout
    await _save_prefs(username, prefs)
    return {"layout": body.layout}


@router.get("/dashboard-layout-v2")
async def get_dashboard_layout_v2(username: str = Depends(require_auth)) -> dict:
    prefs = await _get_prefs(username)
    stored = prefs.get("dashboard_layout_v2")
    if not isinstance(stored, dict) or stored.get("version") != 2:
        return {"version": 2, "items": None}
    return stored


@router.put("/dashboard-layout-v2")
async def update_dashboard_layout_v2(
    body: DashboardLayoutV2Update,
    username: str = Depends(require_auth),
) -> dict:
    payload = body.model_dump()
    # Mantener el layout histórico sincronizado permite que clientes antiguos
    # sigan abriendo una versión razonable del dashboard.
    legacy_layout = list(dict.fromkeys(item.type for item in body.items))
    prefs = await _get_prefs(username)
    prefs["dashboard_layout_v2"] = payload
    prefs["dashboard_layout"] = legacy_layout
    await _save_prefs(username, prefs)
    return payload


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
            raise APIError(
                422,
                "invalid_field",
                "log_retention_days debe estar entre 1 y 365",
                extra={"field": "log_retention_days"},
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
    "stress_max_concurrency": 0,  # máx peticiones en vuelo simultáneo en Centinel (0=sin límite)
    # Si está activo, "/" muestra una landing de presentación del proyecto en
    # vez de redirigir directo a /login/. Pensado para el despliegue SaaS
    # público; en instalaciones self-hosted lo natural es dejarlo desactivado.
    "landing_enabled": False,
    # Controla solo la VISIBILIDAD de los botones de login social en /login/
    # — hoy son placeholders deshabilitados ("Próximamente"), no hay
    # integración OAuth real todavía. Default True: preserva el comportamiento
    # visual actual (los 3 botones ya se muestran) en instalaciones existentes.
    "oauth_google_enabled": True,
    "oauth_apple_enabled": True,
    "oauth_microsoft_enabled": True,
    # Solo lectura desde aquí — refleja si Watchtower está corriendo o no.
    # Se escribe EXCLUSIVAMENTE desde PUT /api/admin/auto-update (auth.py),
    # nunca desde PUT /api/settings/platform (por eso no está en
    # PlatformConfigUpdate más abajo): así el valor persistido nunca puede
    # desincronizarse de si el contenedor "watchtower" realmente arrancó o
    # paró — solo se guarda tras confirmar la operación contra Docker.
    "auto_update_enabled": True,
}

_VALID_REGISTRATION = {"open", "closed"}


def _read_platform_cfg() -> dict:
    import json as _json

    from app.config.data import SETTINGS_FILE

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
    import json as _json

    from app.config.data import SETTINGS_FILE

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
    landing_enabled: Optional[bool] = None
    stress_max_concurrency: Optional[int] = None
    oauth_google_enabled: Optional[bool] = None
    oauth_apple_enabled: Optional[bool] = None
    oauth_microsoft_enabled: Optional[bool] = None


@router.get("/platform/public")
async def get_platform_config_public() -> dict:
    """Devuelve solo los campos públicos de la config de plataforma (sin autenticación).
    Usado por login, registro y otras páginas públicas para adaptar el UI."""
    cfg = _read_platform_cfg()
    return {
        "billing_enabled": cfg.get("billing_enabled", False),
        "guest_enabled": cfg.get("guest_enabled", True),
        "registration": cfg.get("registration", "open"),
        "landing_enabled": cfg.get("landing_enabled", False),
        "oauth_google_enabled": cfg.get("oauth_google_enabled", True),
        "oauth_apple_enabled": cfg.get("oauth_apple_enabled", True),
        "oauth_microsoft_enabled": cfg.get("oauth_microsoft_enabled", True),
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
        raise APIError(
            422,
            "invalid_field",
            "registration debe ser 'open' o 'closed'",
            extra={"field": "registration"},
        )
    if "max_users" in update and update["max_users"] < 0:
        raise APIError(
            422,
            "invalid_field",
            "max_users debe ser >= 0",
            extra={"field": "max_users"},
        )
    if "max_concurrent_sessions" in update and update["max_concurrent_sessions"] < 0:
        raise APIError(
            422,
            "invalid_field",
            "max_concurrent_sessions debe ser >= 0",
            extra={"field": "max_concurrent_sessions"},
        )
    if "log_retention_days" in update and not (
        1 <= update["log_retention_days"] <= 365
    ):
        raise APIError(
            422,
            "invalid_field",
            "log_retention_days debe estar entre 1 y 365",
            extra={"field": "log_retention_days"},
        )
    # Solo un default de UI (prefill del slider "Concurrencia máx" en Centinel)
    # — no limita lo que un test concreto puede pedir, así que no está ligado
    # al backstop técnico de centinel.py (CENTINEL_THREAD_CEILING).
    if "stress_max_concurrency" in update and not (
        0 <= update["stress_max_concurrency"] <= 100_000
    ):
        raise APIError(
            422,
            "invalid_field",
            "stress_max_concurrency debe estar entre 0 y 100000",
            extra={"field": "stress_max_concurrency"},
        )

    cfg.update(update)
    _write_platform_cfg(cfg)
    return cfg
