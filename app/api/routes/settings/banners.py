"""Avisos que el admin publica a toda la instalación."""


from __future__ import annotations

from datetime import datetime as _datetime
from datetime import timezone as _timezone

from fastapi import Depends
from pydantic import BaseModel, Field, model_validator

from app.api.routes.auth import require_admin, require_auth
from app.api.routes.settings._router import router
from app.api.routes.settings._shared import (
    _DEFAULTS,
    VALID_LANGUAGES,
    _get_prefs,
)
from app.errors import APIError
from app.services.platform_settings import (
    _read_platform_cfg,
    _write_platform_cfg,
)
from app.utils.generators import generate_date, generate_id


class NotificationBannerMessage(BaseModel):
    es: str = Field(min_length=1, max_length=500)
    en: str = Field(min_length=1, max_length=500)

class NotificationBannerPayload(BaseModel):
    start_at: str
    end_at: str
    message: NotificationBannerMessage

    @model_validator(mode="after")
    def validate_range(self) -> "NotificationBannerPayload":
        try:
            start = _datetime.fromisoformat(self.start_at)
            end = _datetime.fromisoformat(self.end_at)
        except ValueError:
            raise ValueError(
                "start_at/end_at deben ser fechas ISO 8601 válidas"
            ) from None
        if start.tzinfo is None:
            start = start.replace(tzinfo=_timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=_timezone.utc)
        if end <= start:
            raise ValueError("end_at debe ser posterior a start_at")
        self.start_at = start.isoformat()
        self.end_at = end.isoformat()
        return self

def _find_banner(cfg: dict, banner_id: str) -> dict:
    for banner in cfg.get("notification_banners", []):
        if banner.get("id") == banner_id:
            return banner
    raise APIError(404, "not_found", "Banner no encontrado")

@router.get("/notification-banners")
async def list_notification_banners(_: str = Depends(require_admin)) -> list[dict]:
    """Lista completa de banners (pasados, vigentes y futuros) para el panel admin."""
    cfg = _read_platform_cfg()
    return cfg.get("notification_banners", [])

@router.post("/notification-banners")
async def create_notification_banner(
    body: NotificationBannerPayload,
    _: str = Depends(require_admin),
) -> dict:
    cfg = _read_platform_cfg()
    banner = {
        "id": generate_id(),
        "created_at": generate_date(),
        "start_at": body.start_at,
        "end_at": body.end_at,
        "message": body.message.model_dump(),
    }
    cfg.setdefault("notification_banners", []).append(banner)
    _write_platform_cfg(cfg)
    return banner

@router.put("/notification-banners/{banner_id}")
async def update_notification_banner(
    banner_id: str,
    body: NotificationBannerPayload,
    _: str = Depends(require_admin),
) -> dict:
    cfg = _read_platform_cfg()
    banner = _find_banner(cfg, banner_id)
    banner["start_at"] = body.start_at
    banner["end_at"] = body.end_at
    banner["message"] = body.message.model_dump()
    _write_platform_cfg(cfg)
    return banner

@router.delete("/notification-banners/{banner_id}")
async def delete_notification_banner(
    banner_id: str,
    _: str = Depends(require_admin),
) -> dict:
    cfg = _read_platform_cfg()
    _find_banner(cfg, banner_id)
    cfg["notification_banners"] = [
        b for b in cfg.get("notification_banners", []) if b.get("id") != banner_id
    ]
    _write_platform_cfg(cfg)
    return {"ok": True}

@router.get("/notification-banners/active")
async def get_active_notification_banners(
    username: str = Depends(require_auth),
) -> list[dict]:
    """Banners vigentes ahora, con el mensaje ya resuelto en el idioma del
    usuario autenticado — no se exponen ambos idiomas al cliente."""
    prefs = await _get_prefs(username)
    language = prefs.get("language", _DEFAULTS["language"])
    if language not in VALID_LANGUAGES:
        language = _DEFAULTS["language"]
    now = _datetime.now(_timezone.utc)
    cfg = _read_platform_cfg()
    active = []
    for banner in cfg.get("notification_banners", []):
        try:
            start = _datetime.fromisoformat(banner["start_at"])
            end = _datetime.fromisoformat(banner["end_at"])
        except (KeyError, ValueError):
            continue
        if start <= now <= end:
            message = banner.get("message", {})
            active.append(
                {
                    "id": banner.get("id"),
                    "message": message.get(language) or message.get("es", ""),
                }
            )
    return active
