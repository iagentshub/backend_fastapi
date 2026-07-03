"""Rutas de administración — visor de logs (almacenados en SQLite)."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.routes.auth import require_admin
from app.utils import flog
from app.utils.flog import log_db_path

router = APIRouter(prefix="/api/admin/logs", tags=["admin-logs"])

_PAGE_SIZE_DEFAULT = 50
_PAGE_SIZE_MAX = 500


async def _open_log_db() -> Optional[aiosqlite.Connection]:
    """Abre la conexión a logs.sqlite3. Devuelve None si no está disponible."""
    import sqlite3

    p = log_db_path()
    if p is None or not p.exists():
        return None
    conn = await aiosqlite.connect(str(p))
    conn.row_factory = sqlite3.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _build_where(
    date_from: Optional[str],
    date_to: Optional[str],
    ip: Optional[str],
    username: Optional[str],
    level: Optional[str],
    source: Optional[str],
    q: Optional[str],
):
    """Construye la cláusula WHERE y lista de parámetros."""
    clauses: list[str] = []
    params: list = []
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if ip:
        clauses.append("ip LIKE ?")
        params.append(f"%{ip}%")
    if username:
        clauses.append("username LIKE ?")
        params.append(f"%{username}%")
    if level:
        clauses.append("level = ?")
        params.append(level.upper())
    if source:
        clauses.append("source = ?")
        params.append(source.upper())
    if q:
        clauses.append("summary LIKE ?")
        params.append(f"%{q}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@router.get("")
async def list_logs(
    date_from: Optional[str] = Query(None, description="Fecha inicio YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Fecha fin YYYY-MM-DD"),
    ip: Optional[str] = Query(None, description="Filtro parcial de IP"),
    username: Optional[str] = Query(None, description="Filtro parcial de usuario"),
    level: Optional[str] = Query(
        None, description="Nivel exacto: DEBUG/INFO/OK/WARNING/ERROR"
    ),
    source: Optional[str] = Query(None, description="Fuente: BE o FE"),
    q: Optional[str] = Query(None, description="Texto libre en la acción"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_PAGE_SIZE_DEFAULT, ge=1, le=_PAGE_SIZE_MAX),
    _: str = Depends(require_admin),
) -> dict:
    """Logs filtrados y paginados."""
    conn = await _open_log_db()
    if conn is None:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "pages": 0,
        }
    try:
        where, params = _build_where(date_from, date_to, ip, username, level, source, q)
        async with conn.execute(
            f"SELECT COUNT(*) FROM app_logs {where}", params
        ) as cur:
            row = await cur.fetchone()
            total = row[0] if row else 0
        offset = (page - 1) * page_size
        sql = (
            f"SELECT id, ts, date, time, ip, username, level, source, summary "
            f"FROM app_logs {where} ORDER BY ts DESC LIMIT ? OFFSET ?"
        )
        async with conn.execute(sql, params + [page_size, offset]) as cur:
            rows = await cur.fetchall()
        items = [dict(r) for r in rows]
        pages = (total + page_size - 1) // page_size if total else 0
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }
    finally:
        await conn.close()


@router.get("/export")
async def export_logs(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    _: str = Depends(require_admin),
):
    """Exporta los logs filtrados como fichero CSV."""
    conn = await _open_log_db()
    if conn is None:
        raise HTTPException(
            status_code=503, detail="Base de datos de logs no disponible"
        )
    try:
        where, params = _build_where(date_from, date_to, ip, username, level, source, q)
        sql = (
            f"SELECT date, time, ip, username, level, source, summary "
            f"FROM app_logs {where} ORDER BY ts DESC"
        )
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
    finally:
        await conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Fecha", "Hora", "IP", "Usuario", "Nivel", "Fuente", "Acción"])
    for r in rows:
        writer.writerow(
            [
                r["date"],
                r["time"],
                r["ip"],
                r["username"],
                r["level"],
                r["source"],
                r["summary"],
            ]
        )
    buf.seek(0)
    filename = f"logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/summary", response_model=List[Dict])
async def logs_summary(_: str = Depends(require_admin)) -> List[Dict]:
    """Resumen por día: total de entradas, errores y warnings por fuente."""
    conn = await _open_log_db()
    if conn is None:
        return []
    try:
        sql = """
            SELECT date,
                   COUNT(*) as lines,
                   SUM(CASE WHEN level='WARNING' AND source='BE' THEN 1 ELSE 0 END) as be_warnings,
                   SUM(CASE WHEN level='ERROR'   AND source='BE' THEN 1 ELSE 0 END) as be_errors,
                   SUM(CASE WHEN level='WARNING' AND source='FE' THEN 1 ELSE 0 END) as fe_warnings,
                   SUM(CASE WHEN level='ERROR'   AND source='FE' THEN 1 ELSE 0 END) as fe_errors,
                   SUM(CASE WHEN level='WARNING' THEN 1 ELSE 0 END) as warnings,
                   SUM(CASE WHEN level='ERROR'   THEN 1 ELSE 0 END) as errors
            FROM app_logs
            GROUP BY date
            ORDER BY date DESC
        """
        async with conn.execute(sql) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()


class _ClientLog(BaseModel):
    level: str
    message: str


@router.post("/client")
async def client_log(
    entry: _ClientLog,
    request: Request,
    admin_user: str = Depends(require_admin),
) -> dict:
    """Recibe una entrada de log desde el frontend y la almacena."""
    ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "")
        or (request.client.host if request.client else "-")
        or "-"
    )
    msg = f"[frontend] {entry.message}"
    lvl = entry.level.upper()
    if lvl == "ERROR":
        flog.error(msg, ip=ip, username=admin_user, source="FE")
    elif lvl == "WARNING":
        flog.warning(msg, ip=ip, username=admin_user, source="FE")
    else:
        flog.info(msg, ip=ip, username=admin_user, source="FE")
    return {"ok": True}


async def purge_old_logs(retention_days: Optional[int] = None) -> int:
    """Purga entradas más antiguas que retention_days. Lee la preferencia del admin si no se especifica."""
    if retention_days is None:
        try:
            from app.storage.db import open_db

            async with open_db() as conn:
                row = await conn.fetchone(
                    "SELECT preferences FROM users WHERE role = 'admin' LIMIT 1"
                )
            prefs = json.loads(row["preferences"]) if row and row["preferences"] else {}
            retention_days = int(prefs.get("log_retention_days", 30))
        except Exception:
            retention_days = 30

    conn = await _open_log_db()
    if conn is None:
        return 0
    try:
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        async with conn.execute(
            "DELETE FROM app_logs WHERE date < ?", (cutoff,)
        ) as cur:
            deleted = cur.rowcount
        await conn.commit()
        if deleted:
            flog.ok(
                f"[logs] {deleted} entradas purgadas (retención: {retention_days} días)"
            )
        return deleted
    finally:
        await conn.close()
