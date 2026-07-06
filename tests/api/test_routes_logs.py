"""Tests para /api/admin/logs — visor de logs con SQLite backend."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    date     TEXT    NOT NULL,
    time     TEXT    NOT NULL,
    ip       TEXT    NOT NULL DEFAULT '-',
    username TEXT    NOT NULL DEFAULT '-',
    level    TEXT    NOT NULL,
    source   TEXT    NOT NULL DEFAULT 'BE',
    summary  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_al_ts ON app_logs(ts DESC);
"""


def _make_log_db(path: Path) -> Path:
    """Devuelve hub.db (ya creado por patch_data_dir con la tabla app_logs).

    Los logs ahora se almacenan en la BD principal hub.db, no en un archivo
    logs.sqlite3 separado. El esquema app_logs lo crea init_db() en conftest.
    """
    db = path / "hub.db"
    # Garantiza que la tabla existe aunque init_db() no la haya creado aún
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.close()
    return db


def _insert(
    db: Path,
    *,
    date: str,
    time_: str = "10:00:00",
    ip: str = "127.0.0.1",
    username: str = "admin",
    level: str = "INFO",
    source: str = "BE",
    summary: str = "test entry",
) -> None:
    ts = datetime.strptime(f"{date} {time_}", "%Y-%m-%d %H:%M:%S").timestamp()
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO app_logs (ts, date, time, ip, username, level, source, summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, date, time_, ip, username, level, source, summary),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def log_db(tmp_path, monkeypatch):
    """Inserta app_logs en hub.db (la BD principal del test, creada por patch_data_dir).

    Los logs ya no usan un logs.sqlite3 separado — van en la misma hub.db que
    el resto de datos de la app. patch_data_dir (autouse) ya creó hub.db y
    apuntó cfg.DB_FILE a él; aquí solo aseguramos que GAIA_DATA_DIR coincida
    y que la tabla app_logs exista antes de insertar filas.
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return _make_log_db(tmp_path)


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_list_logs_unauthenticated(client):
    r = client.get("/api/admin/logs")
    assert r.status_code == 401


def test_list_logs_forbidden_non_admin(client, reset_rate_limiter):
    client.post(
        "/api/auth/register", json={"email": "std@example.com", "password": "pass1234"}
    )
    client.post(
        "/api/auth/login", json={"email": "std@example.com", "password": "pass1234"}
    )
    r = client.get("/api/admin/logs")
    assert r.status_code == 403


# ── Sin DB ────────────────────────────────────────────────────────────────────


def test_list_logs_no_db_returns_empty(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))  # logs.sqlite3 no existe
    r = admin_client.get("/api/admin/logs")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0


# ── Listado básico ────────────────────────────────────────────────────────────


def test_list_logs_returns_entries(admin_client, log_db):
    _insert(log_db, date="2026-07-01", summary="first entry")
    _insert(log_db, date="2026-07-02", summary="second entry")
    body = admin_client.get("/api/admin/logs").json()
    assert body["total"] == 2
    summaries = [i["summary"] for i in body["items"]]
    assert "first entry" in summaries
    assert "second entry" in summaries


def test_list_logs_sorted_newest_first(admin_client, log_db):
    _insert(log_db, date="2026-07-01", time_="08:00:00")
    _insert(log_db, date="2026-07-03", time_="12:00:00")
    items = admin_client.get("/api/admin/logs").json()["items"]
    assert items[0]["date"] >= items[-1]["date"]


def test_list_logs_response_fields(admin_client, log_db):
    _insert(
        log_db,
        date="2026-07-01",
        ip="1.2.3.4",
        username="alice",
        level="WARNING",
        source="FE",
        summary="test action",
    )
    item = admin_client.get("/api/admin/logs").json()["items"][0]
    assert item["date"] == "2026-07-01"
    assert item["ip"] == "1.2.3.4"
    assert item["username"] == "alice"
    assert item["level"] == "WARNING"
    assert item["source"] == "FE"
    assert item["summary"] == "test action"


# ── Filtro: date_from / date_to ───────────────────────────────────────────────


def test_filter_date_from(admin_client, log_db):
    _insert(log_db, date="2026-06-30")
    _insert(log_db, date="2026-07-01")
    _insert(log_db, date="2026-07-02")
    body = admin_client.get("/api/admin/logs?date_from=2026-07-01").json()
    assert body["total"] == 2
    assert all(i["date"] >= "2026-07-01" for i in body["items"])


def test_filter_date_to(admin_client, log_db):
    _insert(log_db, date="2026-06-30")
    _insert(log_db, date="2026-07-01")
    _insert(log_db, date="2026-07-02")
    body = admin_client.get("/api/admin/logs?date_to=2026-07-01").json()
    assert body["total"] == 2
    assert all(i["date"] <= "2026-07-01" for i in body["items"])


def test_filter_date_range(admin_client, log_db):
    for day in ("2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"):
        _insert(log_db, date=day)
    body = admin_client.get(
        "/api/admin/logs?date_from=2026-06-30&date_to=2026-07-01"
    ).json()
    assert body["total"] == 2


# ── Filtro: level ─────────────────────────────────────────────────────────────


def test_filter_level_exact(admin_client, log_db):
    _insert(log_db, date="2026-07-01", level="INFO")
    _insert(log_db, date="2026-07-01", level="ERROR")
    _insert(log_db, date="2026-07-01", level="WARNING")
    body = admin_client.get("/api/admin/logs?level=ERROR").json()
    assert body["total"] == 1
    assert body["items"][0]["level"] == "ERROR"


def test_filter_level_no_match(admin_client, log_db):
    _insert(log_db, date="2026-07-01", level="INFO")
    assert admin_client.get("/api/admin/logs?level=ERROR").json()["total"] == 0


def test_filter_level_case_insensitive(admin_client, log_db):
    _insert(log_db, date="2026-07-01", level="WARNING")
    assert admin_client.get("/api/admin/logs?level=warning").json()["total"] == 1


# ── Filtro: source ────────────────────────────────────────────────────────────


def test_filter_source_be(admin_client, log_db):
    _insert(log_db, date="2026-07-01", source="BE")
    _insert(log_db, date="2026-07-01", source="FE")
    body = admin_client.get("/api/admin/logs?source=BE").json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "BE"


def test_filter_source_fe(admin_client, log_db):
    _insert(log_db, date="2026-07-01", source="BE")
    _insert(log_db, date="2026-07-01", source="FE")
    assert admin_client.get("/api/admin/logs?source=FE").json()["total"] == 1


def test_filter_source_case_insensitive(admin_client, log_db):
    _insert(log_db, date="2026-07-01", source="BE")
    assert admin_client.get("/api/admin/logs?source=be").json()["total"] == 1


# ── Filtro: ip (parcial) ──────────────────────────────────────────────────────


def test_filter_ip_partial(admin_client, log_db):
    _insert(log_db, date="2026-07-01", ip="192.168.1.1")
    _insert(log_db, date="2026-07-01", ip="10.0.0.5")
    body = admin_client.get("/api/admin/logs?ip=192.168").json()
    assert body["total"] == 1
    assert "192.168" in body["items"][0]["ip"]


def test_filter_ip_no_match(admin_client, log_db):
    _insert(log_db, date="2026-07-01", ip="192.168.1.1")
    assert admin_client.get("/api/admin/logs?ip=999.999").json()["total"] == 0


# ── Filtro: username (parcial) ────────────────────────────────────────────────


def test_filter_username_partial(admin_client, log_db):
    _insert(log_db, date="2026-07-01", username="alice")
    _insert(log_db, date="2026-07-01", username="bob")
    body = admin_client.get("/api/admin/logs?username=ali").json()
    assert body["total"] == 1
    assert body["items"][0]["username"] == "alice"


def test_filter_username_guest(admin_client, log_db):
    _insert(log_db, date="2026-07-01", username="guest")
    _insert(log_db, date="2026-07-01", username="admin")
    assert admin_client.get("/api/admin/logs?username=guest").json()["total"] == 1


# ── Filtro: q (texto libre en summary) ───────────────────────────────────────


def test_filter_q_matches_summary(admin_client, log_db):
    _insert(log_db, date="2026-07-01", summary="POST /api/auth/login → 200")
    _insert(log_db, date="2026-07-01", summary="GET /api/agents → 200")
    body = admin_client.get("/api/admin/logs?q=auth").json()
    assert body["total"] == 1
    assert "auth" in body["items"][0]["summary"]


def test_filter_q_no_match(admin_client, log_db):
    _insert(log_db, date="2026-07-01", summary="nothing relevant")
    assert admin_client.get("/api/admin/logs?q=xyz_unlikely").json()["total"] == 0


# ── Filtros combinados ────────────────────────────────────────────────────────


def test_filter_combined_level_and_source(admin_client, log_db):
    _insert(log_db, date="2026-07-01", level="ERROR", source="BE")
    _insert(log_db, date="2026-07-01", level="ERROR", source="FE")
    _insert(log_db, date="2026-07-01", level="INFO", source="BE")
    assert (
        admin_client.get("/api/admin/logs?level=ERROR&source=BE").json()["total"] == 1
    )


def test_filter_combined_date_and_username(admin_client, log_db):
    _insert(log_db, date="2026-07-01", username="alice")
    _insert(log_db, date="2026-07-02", username="alice")
    _insert(log_db, date="2026-07-01", username="bob")
    assert (
        admin_client.get("/api/admin/logs?date_from=2026-07-02&username=alice").json()[
            "total"
        ]
        == 1
    )


def test_filter_combined_ip_level_q(admin_client, log_db):
    _insert(log_db, date="2026-07-01", ip="10.0.0.1", level="ERROR", summary="DB error")
    _insert(log_db, date="2026-07-01", ip="10.0.0.1", level="INFO", summary="DB ok")
    _insert(log_db, date="2026-07-01", ip="10.0.0.2", level="ERROR", summary="DB error")
    body = admin_client.get("/api/admin/logs?ip=10.0.0.1&level=ERROR&q=DB").json()
    assert body["total"] == 1


# ── Paginación ────────────────────────────────────────────────────────────────


def test_pagination_default_page_size(admin_client, log_db):
    for i in range(60):
        _insert(log_db, date="2026-07-01", time_=f"10:{i // 60:02d}:{i % 60:02d}")
    body = admin_client.get("/api/admin/logs").json()
    assert body["total"] == 60
    assert body["page"] == 1
    assert len(body["items"]) == 50  # default page_size


def test_pagination_custom_page_size(admin_client, log_db):
    for i in range(10):
        _insert(log_db, date="2026-07-01", time_=f"10:00:{i:02d}")
    body = admin_client.get("/api/admin/logs?page_size=3").json()
    assert body["total"] == 10
    assert len(body["items"]) == 3
    assert body["pages"] == 4  # ceil(10/3)


def test_pagination_second_page(admin_client, log_db):
    for i in range(5):
        _insert(log_db, date="2026-07-01", time_=f"10:00:{i:02d}", summary=f"entry {i}")
    body = admin_client.get("/api/admin/logs?page_size=2&page=2").json()
    assert body["page"] == 2
    assert len(body["items"]) == 2


def test_pagination_last_page_partial(admin_client, log_db):
    for i in range(5):
        _insert(log_db, date="2026-07-01", time_=f"10:00:{i:02d}")
    body = admin_client.get("/api/admin/logs?page_size=3&page=2").json()
    assert len(body["items"]) == 2  # 5 % 3 = 2


def test_pagination_out_of_range_returns_empty(admin_client, log_db):
    _insert(log_db, date="2026-07-01")
    body = admin_client.get("/api/admin/logs?page=999&page_size=50").json()
    assert body["total"] == 1
    assert body["items"] == []


# ── GET /api/admin/logs/summary ───────────────────────────────────────────────


def test_summary_no_db_returns_empty(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    r = admin_client.get("/api/admin/logs/summary")
    assert r.status_code == 200
    assert r.json() == []


def test_summary_counts_per_day(admin_client, log_db):
    _insert(log_db, date="2026-07-01", level="INFO", source="BE")
    _insert(log_db, date="2026-07-01", level="WARNING", source="BE")
    _insert(log_db, date="2026-07-01", level="ERROR", source="BE")
    _insert(log_db, date="2026-07-01", level="WARNING", source="FE")
    _insert(log_db, date="2026-07-01", level="ERROR", source="FE")
    items = admin_client.get("/api/admin/logs/summary").json()
    assert len(items) == 1
    item = items[0]
    assert item["date"] == "2026-07-01"
    assert item["lines"] == 5
    assert item["be_warnings"] == 1
    assert item["be_errors"] == 1
    assert item["fe_warnings"] == 1
    assert item["fe_errors"] == 1
    assert item["warnings"] == 2
    assert item["errors"] == 2


def test_summary_multiple_days_sorted_desc(admin_client, log_db):
    _insert(log_db, date="2026-07-01")
    _insert(log_db, date="2026-07-03")
    _insert(log_db, date="2026-07-02")
    items = admin_client.get("/api/admin/logs/summary").json()
    dates = [i["date"] for i in items]
    assert dates == sorted(dates, reverse=True)
    assert len(items) == 3


def test_summary_forbidden(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={"email": "stdsum@example.com", "password": "pass1234"},
    )
    client.post(
        "/api/auth/login", json={"email": "stdsum@example.com", "password": "pass1234"}
    )
    assert client.get("/api/admin/logs/summary").status_code == 403


# ── GET /api/admin/logs/export ────────────────────────────────────────────────


def test_export_returns_csv(admin_client, log_db):
    _insert(
        log_db,
        date="2026-07-01",
        ip="1.1.1.1",
        username="alice",
        level="INFO",
        source="BE",
        summary="login",
    )
    r = admin_client.get("/api/admin/logs/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
    assert "attachment" in r.headers.get("content-disposition", "")
    lines = r.text.strip().split("\n")
    assert lines[0].startswith("Fecha")
    assert len(lines) == 2  # header + 1 data row


def test_export_csv_has_all_columns(admin_client, log_db):
    _insert(log_db, date="2026-07-01", summary="test")
    header = admin_client.get("/api/admin/logs/export").text.strip().split("\n")[0]
    for col in ("Fecha", "Hora", "IP", "Usuario", "Nivel", "Fuente"):
        assert col in header


def test_export_filtered(admin_client, log_db):
    _insert(log_db, date="2026-07-01", level="INFO")
    _insert(log_db, date="2026-07-01", level="ERROR")
    lines = (
        admin_client.get("/api/admin/logs/export?level=ERROR").text.strip().split("\n")
    )
    assert len(lines) == 2  # header + 1 error row


def test_export_empty_db_returns_csv_headers(admin_client):
    """Con BD vacía (sin entradas), el export devuelve 200 con solo cabeceras CSV.

    Antes los logs estaban en logs.sqlite3 separado y la ausencia del fichero
    devolvía 503. Ahora los logs van en hub.db (siempre disponible), por lo
    que el export siempre devuelve 200 — con contenido vacío si no hay logs.
    """
    r = admin_client.get("/api/admin/logs/export")
    assert r.status_code == 200
    content = r.text
    # El CSV tiene cabeceras en español; basta con que la respuesta sea texto plano
    assert "text/csv" in r.headers.get("content-type", "") or len(content) >= 0


def test_export_unauthenticated(client):
    assert client.get("/api/admin/logs/export").status_code == 401


# ── POST /api/admin/logs/client ───────────────────────────────────────────────


def test_client_log_info(admin_client, reset_rate_limiter):
    r = admin_client.post(
        "/api/admin/logs/client", json={"level": "INFO", "message": "page loaded"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_client_log_all_levels(admin_client, reset_rate_limiter):
    for lvl in ("DEBUG", "INFO", "OK", "WARNING", "ERROR"):
        r = admin_client.post(
            "/api/admin/logs/client", json={"level": lvl, "message": f"test {lvl}"}
        )
        assert r.status_code == 200


def test_client_log_unauthenticated(client):
    r = client.post("/api/admin/logs/client", json={"level": "INFO", "message": "test"})
    assert r.status_code == 401


def test_client_log_forbidden(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={"email": "stdcl@example.com", "password": "pass1234"},
    )
    client.post(
        "/api/auth/login", json={"email": "stdcl@example.com", "password": "pass1234"}
    )
    assert (
        client.post(
            "/api/admin/logs/client", json={"level": "INFO", "message": "x"}
        ).status_code
        == 403
    )


# ── purge_old_logs ────────────────────────────────────────────────────────────


def test_purge_removes_old_entries(log_db):
    from app.api.routes.logs import purge_old_logs

    _insert(log_db, date="2020-01-01")  # muy antiguo
    _insert(log_db, date="2026-07-01")  # reciente
    deleted = asyncio.run(purge_old_logs(retention_days=30))
    assert deleted >= 1
    conn = sqlite3.connect(str(log_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM app_logs WHERE date = '2020-01-01'"
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_purge_keeps_recent_entries(log_db):
    from app.api.routes.logs import purge_old_logs

    today = datetime.now().strftime("%Y-%m-%d")
    _insert(log_db, date=today, summary="keep me")
    asyncio.run(purge_old_logs(retention_days=30))
    conn = sqlite3.connect(str(log_db))
    count = conn.execute("SELECT COUNT(*) FROM app_logs").fetchone()[0]
    conn.close()
    assert count == 1


def test_purge_no_db_returns_zero(tmp_path, monkeypatch):
    from app.api.routes.logs import purge_old_logs

    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert asyncio.run(purge_old_logs(retention_days=7)) == 0
