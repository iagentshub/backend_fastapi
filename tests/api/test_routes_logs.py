"""Tests for GET/POST /api/admin/logs endpoints."""
from __future__ import annotations

from pathlib import Path


def _logs_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect GAIA_DATA_DIR to an isolated tmp dir and return the logs sub-dir."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    d = tmp_path / "logs"
    d.mkdir()
    return d


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_logs_empty_dir(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    r = admin_client.get("/api/admin/logs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_logs_no_dir(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "nonexistent"))
    r = admin_client.get("/api/admin/logs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_logs_returns_stems(admin_client, tmp_path, monkeypatch):
    d = _logs_dir(tmp_path, monkeypatch)
    (d / "20260516.log").write_text("line\n")
    (d / "20260515.log").write_text("line\n")
    r = admin_client.get("/api/admin/logs")
    assert r.status_code == 200
    dates = r.json()
    assert "20260516" in dates
    assert "20260515" in dates


def test_list_logs_sorted_desc(admin_client, tmp_path, monkeypatch):
    d = _logs_dir(tmp_path, monkeypatch)
    for day in ("20260514", "20260516", "20260515"):
        (d / f"{day}.log").write_text("x\n")
    dates = admin_client.get("/api/admin/logs").json()
    assert dates == sorted(dates, reverse=True)


def test_list_logs_forbidden(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "stdlogs@example.com", "password": "pass1234"})
    client.post("/api/auth/login", json={"email": "stdlogs@example.com", "password": "pass1234"})
    r = client.get("/api/admin/logs")
    assert r.status_code == 403


def test_list_logs_unauthenticated(client):
    r = client.get("/api/admin/logs")
    assert r.status_code == 401


# ── get log content ──────────────────────────────────────────────────────────


def test_get_log_returns_content(admin_client, tmp_path, monkeypatch):
    d = _logs_dir(tmp_path, monkeypatch)
    (d / "20260516.log").write_text("2026-05-16 10:00:00 - INFO   - hello\n")
    r = admin_client.get("/api/admin/logs/20260516")
    assert r.status_code == 200
    assert "hello" in r.text


def test_get_log_not_found(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    (tmp_path / "logs").mkdir()
    r = admin_client.get("/api/admin/logs/20010101")
    assert r.status_code == 404


def test_get_log_invalid_date_non_digit(admin_client):
    r = admin_client.get("/api/admin/logs/notadate")
    assert r.status_code == 400


def test_get_log_invalid_date_wrong_length(admin_client):
    r = admin_client.get("/api/admin/logs/2026051")  # 7 digits
    assert r.status_code == 400


def test_get_log_forbidden(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "stdlogs2@example.com", "password": "pass1234"})
    client.post("/api/auth/login", json={"email": "stdlogs2@example.com", "password": "pass1234"})
    r = client.get("/api/admin/logs/20260516")
    assert r.status_code == 403


# ── summary ──────────────────────────────────────────────────────────────────


def test_summary_empty(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    r = admin_client.get("/api/admin/logs/summary")
    assert r.status_code == 200
    assert r.json() == []


def test_summary_counts(admin_client, tmp_path, monkeypatch):
    d = _logs_dir(tmp_path, monkeypatch)
    content = (
        "2026-05-16 10:00:00 - INFO    - msg\n"
        "2026-05-16 10:00:01 - WARNING - watch out\n"
        "2026-05-16 10:00:02 - ERROR   - boom\n"
        "2026-05-16 10:00:03 - ERROR   - again\n"
        "2026-05-16 10:00:04 - WARNING - [frontend] fe warn\n"
        "2026-05-16 10:00:05 - ERROR   - [frontend] fe err\n"
    )
    (d / "20260516.log").write_text(content)
    r = admin_client.get("/api/admin/logs/summary")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    item = items[0]
    assert item["date"] == "20260516"
    assert item["lines"] == 6
    assert item["be_warnings"] == 1
    assert item["be_errors"] == 2
    assert item["fe_warnings"] == 1
    assert item["fe_errors"] == 1
    assert item["warnings"] == 2
    assert item["errors"] == 3


def test_summary_multiple_files(admin_client, tmp_path, monkeypatch):
    d = _logs_dir(tmp_path, monkeypatch)
    (d / "20260516.log").write_text("2026-05-16 10:00:00 - ERROR - oops\n")
    (d / "20260515.log").write_text("2026-05-15 08:00:00 - INFO  - ok\n")
    items = admin_client.get("/api/admin/logs/summary").json()
    dates = [i["date"] for i in items]
    assert dates == sorted(dates, reverse=True)
    assert len(items) == 2


def test_summary_forbidden(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "stdsum@example.com", "password": "pass1234"})
    client.post("/api/auth/login", json={"email": "stdsum@example.com", "password": "pass1234"})
    r = client.get("/api/admin/logs/summary")
    assert r.status_code == 403


# ── client log ───────────────────────────────────────────────────────────────


def test_client_log_authenticated(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "loguser@example.com", "password": "pass1234"})
    client.post("/api/auth/login", json={"email": "loguser@example.com", "password": "pass1234"})
    r = client.post("/api/admin/logs/client", json={"level": "INFO", "message": "frontend test"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_client_log_all_levels(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "loglvl@example.com", "password": "pass1234"})
    client.post("/api/auth/login", json={"email": "loglvl@example.com", "password": "pass1234"})
    for level in ("DEBUG", "INFO", "OK", "WARNING", "ERROR"):
        r = client.post("/api/admin/logs/client", json={"level": level, "message": f"test {level}"})
        assert r.status_code == 200


def test_client_log_unauthenticated(client):
    r = client.post("/api/admin/logs/client", json={"level": "INFO", "message": "test"})
    assert r.status_code == 401
