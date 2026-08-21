"""Rutas de administración — visor de logs (almacenados en la BD principal)."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.routes.auth import require_admin
from app.services.platform_settings import _read_platform_cfg
from app.sql import sql
from app.storage.db import open_db
from app.utils import flog
from app.utils.net import client_ip as _client_ip

router = APIRouter(prefix="/api/admin/logs", tags=["admin-logs"])

_PAGE_SIZE_DEFAULT = 50
_PAGE_SIZE_MAX = 500


def _build_where(
    date_from: Optional[str],
    date_to: Optional[str],
    ip: Optional[str],
    username: Optional[str],
    level: Optional[str],
    source: Optional[str],
    category: Optional[str],
    action: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    outcome: Optional[str],
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
    if category:
        clauses.append("category = ?")
        params.append(category.upper())
    if action:
        clauses.append("action = ?")
        params.append(action.lower())
    if resource_type:
        clauses.append("resource_type = ?")
        params.append(resource_type.lower())
    if resource_id:
        clauses.append("resource_id = ?")
        params.append(resource_id)
    if outcome:
        clauses.append("outcome = ?")
        params.append(outcome.upper())
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
    category: Optional[str] = Query(
        None, description="Categoría exacta: DIAGNOSTIC o AUDIT"
    ),
    action: Optional[str] = Query(None, description="Acción auditable exacta"),
    resource_type: Optional[str] = Query(None, description="Tipo de recurso exacto"),
    resource_id: Optional[str] = Query(None, description="ID de recurso exacto"),
    outcome: Optional[str] = Query(
        None, description="Resultado exacto: SUCCESS, DENIED o FAILURE"
    ),
    q: Optional[str] = Query(None, description="Texto libre en la acción"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_PAGE_SIZE_DEFAULT, ge=1, le=_PAGE_SIZE_MAX),
    _: str = Depends(require_admin),
) -> dict:
    """Logs filtrados y paginados."""
    # La escritura en app_logs es por lotes (ver app/utils/flog.py), así que sin
    # esto el visor omitiría hasta el último segundo de actividad — justo lo que
    # busca quien abre el panel para ver qué acaba de pasar. Va en un hilo
    # porque el volcado es síncrono y no puede bloquear el event loop.
    await asyncio.to_thread(flog.flush)
    where, params = _build_where(
        date_from,
        date_to,
        ip,
        username,
        level,
        source,
        category,
        action,
        resource_type,
        resource_id,
        outcome,
        q,
    )
    async with open_db() as conn:
        total = (
            await conn.fetchval(f"SELECT COUNT(*) FROM app_logs {where}", tuple(params))
            or 0
        )
        offset = (page - 1) * page_size
        rows = await conn.fetchall(
            f"SELECT id, ts, date, time, ip, username, level, source, summary, "
            f"category, action, resource_type, resource_id, outcome, details_json "
            f"FROM app_logs {where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params + [page_size, offset]),
        )
    items = [dict(r) for r in rows]
    pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/export")
async def export_logs(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    _: str = Depends(require_admin),
):
    """Exporta los logs filtrados como fichero CSV."""
    where, params = _build_where(
        date_from,
        date_to,
        ip,
        username,
        level,
        source,
        category,
        action,
        resource_type,
        resource_id,
        outcome,
        q,
    )
    async with open_db() as conn:
        rows = await conn.fetchall(
            f"SELECT date, time, ip, username, level, source, category, action, "
            f"resource_type, resource_id, outcome, summary, details_json "
            f"FROM app_logs {where} ORDER BY ts DESC, id DESC",
            tuple(params),
        )

    def _csv_safe(value: Optional[str]) -> str:
        """A3: prevenir CSV/formula injection prefijando con comilla si el valor
        comienza con = + - @ que Excel/LibreOffice interpretan como fórmulas."""
        if not value:
            return ""
        s = str(value)
        if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + s
        return s

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Fecha",
            "Hora",
            "IP",
            "Usuario",
            "Nivel",
            "Fuente",
            "Categoría",
            "Evento",
            "Tipo de recurso",
            "ID de recurso",
            "Resultado",
            "Mensaje",
            "Detalle JSON",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                _csv_safe(r["date"]),
                _csv_safe(r["time"]),
                _csv_safe(r["ip"]),
                _csv_safe(r["username"]),
                _csv_safe(r["level"]),
                _csv_safe(r["source"]),
                _csv_safe(r["category"]),
                _csv_safe(r["action"]),
                _csv_safe(r["resource_type"]),
                _csv_safe(r["resource_id"]),
                _csv_safe(r["outcome"]),
                _csv_safe(r["summary"]),
                _csv_safe(r["details_json"]),
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
    async with open_db() as conn:
        rows = await conn.fetchall(sql("queries/logs:daily_summary"))
    return [dict(r) for r in rows]


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
    ip = _client_ip(request) or "-"  # N4: respetar TRUSTED_PROXIES, evitar spoofing
    msg = f"[frontend] {entry.message}"
    lvl = entry.level.upper()
    if lvl == "ERROR":
        flog.error(msg, ip=ip, username=admin_user, source="FE")
    elif lvl == "WARNING":
        flog.warning(msg, ip=ip, username=admin_user, source="FE")
    else:
        flog.info(msg, ip=ip, username=admin_user, source="FE")
    return {"ok": True}


async def purge_old_logs(
    retention_days: Optional[int] = None,
    audit_retention_days: Optional[int] = None,
) -> int:
    """Purga diagnóstico y auditoría con ciclos de vida independientes."""
    try:
        cfg = _read_platform_cfg()
        retention_days = int(
            retention_days
            if retention_days is not None
            else cfg.get("log_retention_days", 30)
        )
        audit_retention_days = int(
            audit_retention_days
            if audit_retention_days is not None
            else cfg.get("audit_log_retention_days", 365)
        )
    except (OSError, TypeError, ValueError) as exc:
        # Estos valores deciden qué registros se borran: el fallback debe ser
        # visible y conservador para auditoría.
        flog.error(
            "[logs] Retención configurada ilegible; se usan 30 días para "
            f"diagnóstico y 365 para auditoría: {exc}"
        )
        retention_days = 30
        audit_retention_days = 365

    diagnostic_cutoff = (datetime.now() - timedelta(days=retention_days)).strftime(
        "%Y-%m-%d"
    )
    audit_cutoff = (datetime.now() - timedelta(days=audit_retention_days)).strftime(
        "%Y-%m-%d"
    )
    async with open_db() as conn:
        deleted = (
            await conn.fetchval(
                sql("queries/logs:count_expired"),
                (diagnostic_cutoff, audit_cutoff),
            )
            or 0
        )
        if deleted:
            await conn.execute(
                sql("queries/logs:delete_expired"),
                (diagnostic_cutoff, audit_cutoff),
            )
            await conn.commit()
    if deleted:
        flog.ok(
            f"[logs] {deleted} entradas purgadas (diagnóstico: {retention_days} "
            f"días; auditoría: {audit_retention_days} días)"
        )
    return deleted
