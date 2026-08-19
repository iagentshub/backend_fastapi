"""`router` compartido de ajustes — cada submódulo le registra sus rutas."""


from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/settings", tags=["settings"])
