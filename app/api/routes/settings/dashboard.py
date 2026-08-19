"""Disposición del panel: layout v1, layout v2 y configuración de widgets."""


from __future__ import annotations

from typing import Any, List, Literal

from fastapi import Depends
from pydantic import BaseModel, Field, model_validator

from app.api.routes.auth import require_auth
from app.api.routes.settings._router import router
from app.api.routes.settings._shared import (
    _get_prefs,
    _save_prefs,
)
from app.errors import APIError

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
