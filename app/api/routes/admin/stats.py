"""Metadatos de tablas, salud del servidor y `/api/admin/stats`."""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import Depends, Query

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.config.data import AGENTS_DIR as _AGENTS_DIR
from app.config.startup_checks import report as config_report
from app.errors import APIError
from app.sql import sql
from app.storage.db import IS_PG, open_db
from app.utils import flog


@admin_router.get("/metadata/tables")
async def admin_metadata_tables(_: str = Depends(require_admin)) -> list:
    """Metadatos de las tablas: nombre, filas, columnas y tamaño estimado."""
    async with open_db() as conn:
        if IS_PG:
            rows = await conn.fetchall(sql("queries/admin_stats:pg_table_stats"))
            return [
                {
                    "name": r["name"],
                    "rows": r["rows"],
                    "col_count": r["col_count"],
                    "size_bytes": r["size_bytes"],
                }
                for r in rows
            ]
        else:
            tables = await conn.fetchall(
                sql("queries/admin_stats:sqlite_table_names")
            )
            result = []
            for t in tables:
                name = t[0]
                cnt = await conn.fetchval(f'SELECT COUNT(*) FROM "{name}"')
                cols = await conn.fetchall(f'PRAGMA table_info("{name}")')
                try:
                    sz = await conn.fetchval(
                        sql("queries/admin_stats:sqlite_table_size"), (name,)
                    )
                except Exception:  # noqa: BLE001 — dbstat puede no estar compilado
                    sz = None
                result.append(
                    {
                        "name": name,
                        "rows": cnt or 0,
                        "col_count": len(cols),
                        "size_bytes": sz,
                    }
                )
            return result


_HIDDEN_COLS = frozenset(
    {
        "password_hash",
        "token",
        "reset_token",
        "verification_token",
        "deletion_token",
        "jwt_secret",
        "stripe_secret_key",
    }
)


@admin_router.get("/metadata/tables/{table_name}/data")
async def admin_metadata_table_data(
    table_name: str,
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: str = Depends(require_admin),
) -> dict:
    """Datos paginados de una tabla. Columnas sensibles enmascaradas."""
    async with open_db() as conn:
        valid = {
            r[0]
            for r in await conn.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
                if not IS_PG
                else "SELECT relname FROM pg_stat_user_tables"
            )
        }
        if table_name not in valid:
            raise APIError(
                404, "not_found", "Tabla no encontrada", extra={"resource": "table"}
            )

        if IS_PG:
            col_rows = await conn.fetchall(
                sql("queries/admin_stats:pg_column_names"),
                (table_name,),
            )
            col_names = [r[0] for r in col_rows]
        else:
            col_rows = await conn.fetchall(f'PRAGMA table_info("{table_name}")')
            col_names = [r[1] for r in col_rows]

        if not col_names:
            raise APIError(404, "table_no_columns", "Sin columnas")

        if q:
            cast = "::text" if IS_PG else ""
            clauses = [f'CAST("{c}"{cast} AS TEXT) LIKE ?' for c in col_names]
            where = "WHERE " + " OR ".join(clauses)
            params = [f"%{q}%"] * len(col_names)
        else:
            where, params = "", []

        total = await conn.fetchval(
            f'SELECT COUNT(*) FROM "{table_name}" {where}', tuple(params)
        )
        offset = (page - 1) * page_size
        rows = await conn.fetchall(
            f'SELECT * FROM "{table_name}" {where} LIMIT ? OFFSET ?',
            tuple(params + [page_size, offset]),
        )

        exposed = [c for c in col_names if c not in _HIDDEN_COLS]
        idx_map = [col_names.index(c) for c in exposed]
        data_rows = [
            [
                "[oculto]"
                if col_names[i] in _HIDDEN_COLS
                else (str(row[i]) if row[i] is not None else None)
                for i in idx_map
            ]
            for row in rows
        ]
        pages = (total + page_size - 1) // page_size if total else 0
        return {
            "columns": exposed,
            "rows": data_rows,
            "total": total or 0,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }


def _server_health() -> dict[str, Any]:
    """Disco/memoria/CPU del host — sin dependencias nuevas (evita psutil):
    shutil.disk_usage es stdlib, la memoria se lee de /proc/meminfo (Linux,
    válido en el contenedor de producción) y la CPU se aproxima con la load
    average normalizada por núcleos. Si algo no está disponible (p.ej. correr
    en macOS en local) se devuelve None en ese campo en vez de romper /stats
    entero — este dato es informativo, nunca debe tumbar el panel de admin."""
    import shutil as _shutil

    from app.config.data import DATA_DIR as _DATA_DIR

    health: dict[str, Any] = {
        "disk_used_pct": None,
        "disk_used_gb": None,
        "disk_total_gb": None,
        "memory_used_pct": None,
        "memory_used_gb": None,
        "memory_total_gb": None,
        "cpu_load_pct": None,
        "cpu_cores": None,
    }
    try:
        usage = _shutil.disk_usage(_DATA_DIR)
        health["disk_used_pct"] = round(usage.used / usage.total * 100, 1)
        health["disk_used_gb"] = round(usage.used / 1_073_741_824, 1)
        health["disk_total_gb"] = round(usage.total / 1_073_741_824, 1)
    except OSError as exc:
        flog.debug(f"[admin] Métrica de disco no disponible: {exc}")

    try:
        meminfo: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.strip().split()[0])  # kB
        mem_total = meminfo.get("MemTotal", 0)
        mem_available = meminfo.get("MemAvailable", 0)
        if mem_total:
            mem_used = mem_total - mem_available
            health["memory_used_pct"] = round(mem_used / mem_total * 100, 1)
            health["memory_used_gb"] = round(mem_used / 1_048_576, 1)
            health["memory_total_gb"] = round(mem_total / 1_048_576, 1)
    except (OSError, UnicodeError, ValueError, IndexError) as exc:
        flog.debug(f"[admin] Métrica de memoria no disponible: {exc}")

    try:
        cores = os.cpu_count() or 1
        load_1min = os.getloadavg()[0]
        health["cpu_cores"] = cores
        health["cpu_load_pct"] = round(load_1min / cores * 100, 1)
    except (OSError, AttributeError) as exc:
        flog.debug(f"[admin] Métrica de CPU no disponible: {exc}")

    return health


@admin_router.get("/config-audit")
async def admin_config_audit(_: str = Depends(require_admin)) -> dict[str, Any]:
    """Qué funciones quedan desactivadas y por qué variable.

    Devuelve nombres de variable, nunca sus valores: el objetivo es que un
    despliegue con un typo en `STRIPE_WEBHOOK_SECRET` se vea desde el panel,
    no exponer secretos a quien tenga una sesión de admin abierta.
    """
    return config_report()


@admin_router.get("/stats")
async def admin_stats(_: str = Depends(require_admin)) -> dict[str, Any]:
    import datetime as _dt

    async with open_db() as conn:
        u = await conn.fetchone(
            sql("queries/admin_stats:user_counts")
        )
        users_total, users_active, users_verified = (u[0] or 0, u[1] or 0, u[2] or 0)

        c = await conn.fetchone(
            sql("queries/admin_stats:connection_totals")
        )
        conns_total, tokens_in, tokens_out = (c[0] or 0, c[1] or 0, c[2] or 0)

        knowledge_total = (
            await conn.fetchval(sql("queries/admin_stats:count_knowledge"))
        ) or 0
        conversations_total = (
            await conn.fetchval(sql("queries/admin_stats:count_conversations"))
        ) or 0
        workflows_total = (
            await conn.fetchval(sql("queries/admin_stats:count_workflows"))
        ) or 0

        _today_utc = _dt.datetime.now(_dt.timezone.utc).date()
        cutoff = (_today_utc - _dt.timedelta(days=13)).isoformat()
        today = _today_utc.isoformat()
        try:
            daily_rows = await conn.fetchall(
                sql("queries/admin_stats:tokens_per_day"),
                (cutoff,),
            )
            tokens_daily = [{"day": r[0], "tokens": r[1]} for r in daily_rows]
            # First-run backfill: seed today from cumulative connection totals
            if not tokens_daily and (tokens_in + tokens_out) > 0:
                if IS_PG:
                    await conn.execute(
                        sql("queries/admin_stats:seed_token_daily_pg"),
                        (today,),
                    )
                else:
                    await conn.execute(
                        sql("queries/admin_stats:seed_token_daily_sqlite"),
                        (today,),
                    )
                await conn.commit()
                daily_rows = await conn.fetchall(
                    sql("queries/admin_stats:tokens_per_day"),
                    (cutoff,),
                )
                tokens_daily = [{"day": r[0], "tokens": r[1]} for r in daily_rows]
        except Exception:  # noqa: BLE001 — backfill best-effort, no debe romper /admin/stats
            tokens_daily = []

        # "date" en app_logs se escribe con datetime.now() local (ver
        # app/utils/flog.py) — se usa la misma convención aquí para que
        # "hoy" coincida con lo ya persistido, en vez de la fecha UTC que
        # usa tokens_daily arriba.
        today_local = _dt.datetime.now().strftime("%Y-%m-%d")
        log_rows = await conn.fetchall(
            sql("queries/admin_stats:logs_of_day"),
            (today_local,),
        )

    requests_today = len(log_rows)
    errors_today = 0
    endpoint_error_counts: dict[str, int] = {}
    latency_total = 0
    latency_count = 0
    _latency_re = re.compile(r"\((\d+)ms\)\s*$")
    for level, summary in log_rows:
        match = _latency_re.search(summary)
        if match:
            latency_total += int(match.group(1))
            latency_count += 1
        if level == "ERROR":
            errors_today += 1
            endpoint = summary.split(" → ", 1)[0].strip()
            endpoint_error_counts[endpoint] = endpoint_error_counts.get(endpoint, 0) + 1

    failure_rate_pct = (
        round(errors_today / requests_today * 100, 1) if requests_today else 0.0
    )
    avg_latency_ms = round(latency_total / latency_count) if latency_count else 0
    top_error_endpoint, top_error_count = (
        max(endpoint_error_counts.items(), key=lambda kv: kv[1])
        if endpoint_error_counts
        else (None, 0)
    )

    agents_public = (
        len(list(_AGENTS_DIR.glob("public/*/config.json")))
        if _AGENTS_DIR.exists()
        else 0
    )
    agents_private = (
        len(list(_AGENTS_DIR.glob("private/*/config.json")))
        if _AGENTS_DIR.exists()
        else 0
    )

    from app.config.session import WEBMAIL_URL

    return {
        "users_total": users_total,
        "users_active": users_active,
        "users_verified": users_verified,
        "connections_total": conns_total,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "knowledge_total": knowledge_total,
        "conversations_total": conversations_total,
        "workflows_total": workflows_total,
        "agents_public": agents_public,
        "agents_private": agents_private,
        "webmail_url": WEBMAIL_URL,
        "tokens_daily": tokens_daily,
        "requests_today": requests_today,
        "errors_today": errors_today,
        "failure_rate_pct": failure_rate_pct,
        "avg_latency_ms": avg_latency_ms,
        "top_error_endpoint": top_error_endpoint,
        "top_error_count": top_error_count,
        **_server_health(),
    }
