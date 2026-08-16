"""Estadísticas de plataforma y salud del servidor."""

from __future__ import annotations

from unittest.mock import patch

# ── Admin stats ───────────────────────────────────────────────────────────────


def test_admin_stats(admin_client):
    r = admin_client.get("/api/admin/stats")
    assert r.status_code == 200
    stats = r.json()
    assert "users" in stats or isinstance(stats, dict)


# ── Auditoría de configuración ────────────────────────────────────────────────


def test_config_audit_lista_las_funciones_degradadas(admin_client):
    r = admin_client.get("/api/admin/config-audit")
    assert r.status_code == 200
    informe = r.json()
    assert {"strict", "errors", "warnings", "checks"} <= set(informe)
    claves = {c["key"] for c in informe["checks"]}
    assert {"billing", "smtp", "email_verify", "jwt_secret"} <= claves
    for check in informe["checks"]:
        assert check["severity"] in ("ok", "warning", "error")


def test_config_audit_no_expone_valores(admin_client, monkeypatch):
    """El informe lo ve cualquier admin: nombres de variable, nunca secretos."""
    import app.config.billing as billing_cfg

    monkeypatch.setattr(billing_cfg, "STRIPE_SECRET_KEY", "sk_live_no_debe_salir")
    r = admin_client.get("/api/admin/config-audit")
    assert "sk_live_no_debe_salir" not in r.text


def test_config_audit_exige_admin(client):
    assert client.get("/api/admin/config-audit").status_code in (401, 403)


def _insert_log(
    db_path,
    *,
    date,
    time_="10:00:00",
    level="INFO",
    source="BE",
    summary="GET /api/health → 200 (10ms)",
):
    import sqlite3
    from datetime import datetime as _datetime

    ts = _datetime.strptime(f"{date} {time_}", "%Y-%m-%d %H:%M:%S").timestamp()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO app_logs (ts, date, time, ip, username, level, source, summary) "
        "VALUES (?, ?, ?, '127.0.0.1', 'admin', ?, ?, ?)",
        (ts, date, time_, level, source, summary),
    )
    conn.commit()
    conn.close()


def test_admin_stats_health_no_logs_today(admin_client):
    r = admin_client.get("/api/admin/stats")
    stats = r.json()
    assert stats["requests_today"] == 0
    assert stats["errors_today"] == 0
    assert stats["failure_rate_pct"] == 0.0
    assert stats["avg_latency_ms"] == 0
    assert stats["top_error_endpoint"] is None
    assert stats["top_error_count"] == 0


def test_admin_stats_server_health(admin_client):
    """Disco siempre disponible (shutil es stdlib multiplataforma); memoria
    depende de /proc/meminfo (Linux, ausente en runners macOS) así que puede
    venir a None ahí — el contrato es "no rompe /stats", no un valor fijo."""
    r = admin_client.get("/api/admin/stats")
    assert r.status_code == 200
    stats = r.json()

    assert stats["disk_total_gb"] > 0
    assert 0 <= stats["disk_used_pct"] <= 100
    assert 0 <= stats["disk_used_gb"] <= stats["disk_total_gb"]

    if stats["memory_total_gb"] is not None:
        assert stats["memory_total_gb"] > 0
        assert 0 <= stats["memory_used_pct"] <= 100

    if stats["cpu_cores"] is not None:
        assert stats["cpu_cores"] >= 1
        assert stats["cpu_load_pct"] >= 0


def test_server_health_optional_metrics_fail_visibly_without_breaking():
    from app.api.routes.admin.stats import _server_health

    with (
        patch("shutil.disk_usage", side_effect=OSError("sin disco")),
        patch("builtins.open", side_effect=OSError("sin proc")),
        patch(
            "app.api.routes.admin.stats.os.getloadavg",
            side_effect=OSError("sin carga"),
            create=True,
        ),
        patch("app.api.routes.admin.stats.flog.debug") as debug,
    ):
        health = _server_health()

    assert all(value is None for value in health.values())
    assert debug.call_count == 3


def test_admin_stats_health_counts_and_failure_rate(admin_client, tmp_path):
    from datetime import datetime as _datetime

    db = tmp_path / "hub.db"
    today = _datetime.now().strftime("%Y-%m-%d")
    _insert_log(db, date=today, level="INFO", summary="GET /api/agents → 200 (40ms)")
    _insert_log(db, date=today, level="INFO", summary="GET /api/agents → 200 (60ms)")
    _insert_log(
        db, date=today, level="WARNING", summary="POST /api/auth/login → 401 (20ms)"
    )
    _insert_log(
        db,
        date=today,
        level="ERROR",
        summary="POST /api/agents/chat → 500 (120ms)",
    )

    r = admin_client.get("/api/admin/stats")
    stats = r.json()
    assert stats["requests_today"] == 4
    assert stats["errors_today"] == 1
    assert stats["failure_rate_pct"] == 25.0
    assert stats["avg_latency_ms"] == round((40 + 60 + 20 + 120) / 4)
    assert stats["top_error_endpoint"] == "POST /api/agents/chat"
    assert stats["top_error_count"] == 1


def test_admin_stats_health_top_error_endpoint_by_frequency(admin_client, tmp_path):
    from datetime import datetime as _datetime

    db = tmp_path / "hub.db"
    today = _datetime.now().strftime("%Y-%m-%d")
    _insert_log(
        db, date=today, level="ERROR", summary="POST /api/agents/chat → 500 (100ms)"
    )
    _insert_log(
        db, date=today, level="ERROR", summary="POST /api/agents/chat → 500 (110ms)"
    )
    _insert_log(
        db, date=today, level="ERROR", summary="GET /api/knowledge → 500 (90ms)"
    )

    r = admin_client.get("/api/admin/stats")
    stats = r.json()
    assert stats["top_error_endpoint"] == "POST /api/agents/chat"
    assert stats["top_error_count"] == 2
    assert stats["errors_today"] == 3


def test_admin_stats_health_excludes_other_days_and_frontend(admin_client, tmp_path):
    from datetime import datetime as _datetime
    from datetime import timedelta as _timedelta

    db = tmp_path / "hub.db"
    today = _datetime.now().strftime("%Y-%m-%d")
    yesterday = (_datetime.now() - _timedelta(days=1)).strftime("%Y-%m-%d")
    _insert_log(
        db, date=yesterday, level="ERROR", summary="GET /api/old → 500 (50ms)"
    )
    _insert_log(
        db,
        date=today,
        level="ERROR",
        source="FE",
        summary="Uncaught TypeError → 0 (0ms)",
    )

    r = admin_client.get("/api/admin/stats")
    stats = r.json()
    assert stats["requests_today"] == 0
    assert stats["errors_today"] == 0
    assert stats["top_error_endpoint"] is None
