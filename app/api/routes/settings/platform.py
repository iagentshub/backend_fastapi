"""Configuración de plataforma que administra el admin.

`/platform/public` es el subconjunto que puede leer cualquiera: de ahí saca
Flutter el límite de subida en vez de llevar su propia copia del número.
"""


from __future__ import annotations

from typing import Optional

from fastapi import Depends
from pydantic import BaseModel

from app.api.routes.auth import require_admin
from app.api.routes.settings._router import router
from app.api.routes.settings._shared import (
    VALID_THEMES,
    _get_prefs,
    _save_prefs,
)
from app.errors import APIError
from app.services.platform_settings import (
    _VALID_REGISTRATION,
    _read_platform_cfg,
    _write_platform_cfg,
)


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

class PlatformConfigUpdate(BaseModel):
    billing_enabled: Optional[bool] = None
    registration: Optional[str] = None
    max_users: Optional[int] = None
    max_concurrent_sessions: Optional[int] = None
    max_request_bytes: Optional[int] = None
    guest_enabled: Optional[bool] = None
    email_verify: Optional[bool] = None
    users_can_configure_theme: Optional[bool] = None
    default_theme: Optional[str] = None
    log_retention_days: Optional[int] = None
    landing_enabled: Optional[bool] = None
    stress_max_concurrency: Optional[int] = None
    oauth_google_enabled: Optional[bool] = None
    oauth_apple_enabled: Optional[bool] = None
    oauth_microsoft_enabled: Optional[bool] = None
    oauth_github_enabled: Optional[bool] = None
    splash_cycles: Optional[int] = None
    splash_end_on_logo: Optional[bool] = None

@router.get("/platform/public")
async def get_platform_config_public() -> dict:
    """Devuelve solo los campos públicos de la config de plataforma (sin autenticación).
    Usado por login, registro y otras páginas públicas para adaptar el UI."""
    from app.config.providers import GITHUB_OAUTH_CLIENT_ID

    cfg = _read_platform_cfg()
    return {
        # Contrato mínimo para que los clientes distingan un backend iAgents
        # real de un proxy, portal cautivo u otro servidor que responda 200 en
        # esta ruta. Es aditivo para mantener compatibilidad con clientes
        # anteriores.
        "service": "iagentshub",
        "api_version": 1,
        "billing_enabled": cfg.get("billing_enabled", False),
        "guest_enabled": cfg.get("guest_enabled", True),
        "registration": cfg.get("registration", "open"),
        "users_can_configure_theme": cfg.get("users_can_configure_theme", True),
        "default_theme": cfg.get("default_theme", "dark-red"),
        "landing_enabled": cfg.get("landing_enabled", False),
        "oauth_google_enabled": cfg.get("oauth_google_enabled", True),
        "oauth_apple_enabled": cfg.get("oauth_apple_enabled", True),
        "oauth_microsoft_enabled": cfg.get("oauth_microsoft_enabled", True),
        # A diferencia de los tres anteriores (placeholders visuales sin
        # integración real), GitHub sí funciona de verdad — por eso su
        # visibilidad exige AMBAS cosas: que el admin lo tenga activado
        # (cfg, editable en Admin) Y que el servidor tenga credenciales
        # configuradas (GITHUB_OAUTH_CLIENT_ID). Apagar el toggle nunca deja
        # sin acceso a quien ya inició sesión por GitHub: solo oculta el
        # botón en /login/, los endpoints /api/auth/github/* (y el login vía
        # extensión de VS Code, que depende de una sesión web ya abierta,
        # no de este flag) siguen respondiendo igual pase lo que pase aquí.
        "oauth_github_enabled": bool(GITHUB_OAUTH_CLIENT_ID)
        and cfg.get("oauth_github_enabled", True),
        "splash_cycles": cfg.get("splash_cycles", 1),
        "splash_end_on_logo": cfg.get("splash_end_on_logo", True),
        # El cliente valida el tamaño antes de subir para no hacer viajar un
        # fichero que va a rebotar. Sale aquí, y no como constante copiada en
        # Dart, porque el número lo cambia el admin en caliente; el rechazo de
        # verdad lo sigue dando el middleware.
        "max_request_bytes": cfg.get("max_request_bytes", 0),
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
    if "default_theme" in update and update["default_theme"] not in VALID_THEMES:
        raise APIError(
            422,
            "invalid_field",
            f"Tema no válido: {update['default_theme']}",
            extra={"field": "default_theme"},
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
    if "max_request_bytes" in update and update["max_request_bytes"] < 0:
        raise APIError(
            422,
            "invalid_field",
            "max_request_bytes debe ser >= 0 (0 = sin límite)",
            extra={"field": "max_request_bytes"},
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
    if "splash_cycles" in update and not (1 <= update["splash_cycles"] <= 10):
        raise APIError(
            422,
            "invalid_field",
            "splash_cycles debe estar entre 1 y 10",
            extra={"field": "splash_cycles"},
        )

    cfg.update(update)
    _write_platform_cfg(cfg)
    return cfg
