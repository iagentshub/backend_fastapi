"""Rutas de administración — visor de logs."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.api.routes.auth import require_admin, require_auth
from app.utils import flog

router = APIRouter(prefix="/api/admin/logs", tags=["admin-logs"])


class _ClientLog(BaseModel):
    level: str
    message: str


def _log_dir() -> Path:
    data = os.environ.get("GAIA_DATA_DIR", "").strip()
    return Path(data) / "logs" if data else Path("data") / "logs"


@router.get("", response_model=List[str])
async def list_logs(_: str = Depends(require_admin)) -> List[str]:
    """Lista de fechas disponibles, orden descendente (más reciente primero)."""
    d = _log_dir()
    if not d.exists():
        return []
    return sorted([p.stem for p in d.glob("*.log")], reverse=True)


_LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - (\w+)\s+-\s+(.*)")


def _count_levels(lines: list[str]) -> dict:
    """Returns per-source warning/error counts for a list of raw log lines."""
    counts = {"be_warnings": 0, "be_errors": 0, "fe_warnings": 0, "fe_errors": 0}
    for line in lines:
        m = _LOG_LINE.match(line)
        if not m:
            continue
        level, msg = m.group(1), m.group(2)
        is_fe = msg.lstrip().startswith("[frontend]")
        if level == "WARNING":
            if is_fe:
                counts["fe_warnings"] += 1
            else:
                counts["be_warnings"] += 1
        elif level == "ERROR":
            if is_fe:
                counts["fe_errors"] += 1
            else:
                counts["be_errors"] += 1
    counts["warnings"] = counts["be_warnings"] + counts["fe_warnings"]
    counts["errors"] = counts["be_errors"] + counts["fe_errors"]
    return counts


@router.get("/summary", response_model=List[Dict])
async def logs_summary(_: str = Depends(require_admin)) -> List[Dict]:
    """Resumen por fichero: tamaño, líneas, errores y warnings (desglosados por origen)."""
    d = _log_dir()
    if not d.exists():
        return []
    result = []
    for p in sorted(d.glob("*.log"), reverse=True):
        try:
            text = p.read_text(encoding="utf-8")
            lines = [l for l in text.splitlines() if l.strip()]
            counts = _count_levels(lines)
            result.append({
                "date": p.stem,
                "size_bytes": p.stat().st_size,
                "lines": len(lines),
                **counts,
            })
        except OSError:
            pass
    return result


@router.post("/client")
async def client_log(entry: _ClientLog, _: str = Depends(require_auth)) -> dict:
    """Recibe una entrada de log desde el frontend y la escribe en el fichero del día."""
    msg = f"[frontend] {entry.message}"
    lvl = entry.level.upper()
    if lvl == "ERROR":
        flog.error(msg)
    elif lvl == "WARNING":
        flog.warning(msg)
    else:
        flog.info(msg)
    return {"ok": True}


@router.get("/{date}", response_class=PlainTextResponse)
async def get_log(date: str, _: str = Depends(require_admin)) -> str:
    """Contenido de data/logs/{date}.log. date debe ser 8 dígitos (YYYYMMDD)."""
    if not date.isdigit() or len(date) != 8:
        raise HTTPException(status_code=400, detail="Fecha inválida — usa formato YYYYMMDD")
    p = _log_dir() / f"{date}.log"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Log no encontrado")
    return p.read_text(encoding="utf-8")
