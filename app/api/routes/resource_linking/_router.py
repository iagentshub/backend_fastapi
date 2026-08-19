"""`router` compartido de enlazado — cada submódulo le registra sus rutas."""


from __future__ import annotations

from fastapi import APIRouter

# db se importa como MÓDULO a propósito: IS_PG debe leerse en el momento de
# la llamada. Traerlo por valor congela el dialecto en el arranque y el
# monkeypatch de los tests no llega — ver
# tests/storage/test_is_pg_en_tiempo_de_llamada.py.

router = APIRouter(tags=["resource-linking"])
