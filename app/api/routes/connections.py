"""Rutas de conexiones."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.connections import all_providers, get_provider
from app.config.data import CONN_FILE
from app.storage.storage import ConnectionStorage

router = APIRouter(prefix="/api/connections", tags=["connections"])

_storage = ConnectionStorage(CONN_FILE)


# IMPORTANTE: las rutas literales (/providers, /test-all) deben definirse
# ANTES que las rutas con parámetros (/{conn_id}) para que FastAPI las priorice.

@router.get("/providers")
async def list_providers(_: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    """Devuelve las definiciones de campos de cada proveedor para el formulario del frontend."""
    return all_providers()


@router.post("/test-all")
async def test_all_connections(
    request: Request, _: str = Depends(require_auth)
) -> List[Dict[str, Any]]:
    """Testa todas las conexiones (o las indicadas en body.ids)."""
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    ids = body.get("ids") or None
    conns = _storage.list()
    if ids:
        conns = [c for c in conns if c.get("id") in ids]

    async def _test_one(conn: Dict[str, Any]) -> Dict[str, Any]:
        provider = get_provider(conn.get("type") or "")
        if not provider:
            return {"id": conn["id"], "ok": False, "message": "Sin proveedor de test", "detail": ""}
        result = await asyncio.to_thread(provider.test, conn)
        return {"id": conn["id"], "ok": result.ok, "message": result.message, "detail": result.detail}

    return list(await asyncio.gather(*[_test_one(c) for c in conns]))


@router.get("")
async def list_connections(_: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    items = _storage.list()
    return [{k: v for k, v in c.items() if k != "api_key"} for c in items]


@router.post("")
async def save_connection(
    request: Request, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    payload = await request.json()
    conn = _storage.save(payload)
    return {k: v for k, v in conn.items() if k != "api_key"}


@router.delete("/{conn_id}")
async def delete_connection(
    conn_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    if not _storage.delete(conn_id):
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    return {"ok": True}


@router.post("/{conn_id}/test")
async def test_connection(
    conn_id: str, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    conn = _storage.get(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    provider = get_provider(conn.get("type") or "")
    if not provider:
        return {"ok": False, "message": f"Tipo '{conn.get('type')}' sin proveedor de test"}
    result = await asyncio.to_thread(provider.test, conn)
    return {"ok": result.ok, "message": result.message, "detail": result.detail}
