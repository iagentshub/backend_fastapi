"""Shared test fixtures."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def tmp_data_dir():
    """Creates an isolated data directory for all tests."""
    d = Path(tempfile.mkdtemp(prefix="gaia_test_"))
    (d / "connections").mkdir()
    (d / "agents").mkdir()
    (d / "skills").mkdir()
    (d / "memory").mkdir()
    (d / "settings.json").write_text(
        json.dumps({"jwt_secret": "test-secret-key-for-tests-only"}),
        encoding="utf-8",
    )
    (d / "connections" / "connections.json").write_text("[]", encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def patch_data_dir(tmp_data_dir, tmp_path, monkeypatch):
    """Redirect GAIA_DATA_DIR to the temp directory before each test.

    Uses a per-test SQLite DB file (via tmp_path) so tests don't share state,
    while keeping shared fixtures (settings.json, etc.) from tmp_data_dir.
    Forces SQLite mode (DATABASE_URL='').
    """
    # Per-test isolated DB — avoids cross-test username/data collisions
    db_file = tmp_path / "hub.db"

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_data_dir))

    # Patch config
    import app.config.data as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_data_dir)
    monkeypatch.setattr(cfg, "DB_FILE", db_file)
    monkeypatch.setattr(cfg, "CONN_FILE", tmp_data_dir / "connections" / "connections.json")
    monkeypatch.setattr(cfg, "AGENTS_DIR", tmp_data_dir / "agents")
    monkeypatch.setattr(cfg, "SKILLS_DIR", tmp_data_dir / "skills")
    monkeypatch.setattr(cfg, "MEMORY_DIR", tmp_data_dir / "memory")
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_data_dir / "settings.json")

    # Patch auth module paths
    import app.auth.auth as auth_mod
    monkeypatch.setattr(auth_mod, "SETTINGS_FILE", tmp_data_dir / "settings.json")
    monkeypatch.setattr(auth_mod, "DB_FILE", db_file)

    # Reset the SQLite connection pool so each test gets a fresh DB file
    import app.storage.db as db_mod
    monkeypatch.setattr(db_mod, "IS_PG", False)
    monkeypatch.setattr(db_mod, "PH", "?")
    old_pool = db_mod._sqlite_pool.copy()
    db_mod._sqlite_pool.clear()
    yield
    # Close and remove connections opened during this test
    for conn in list(db_mod._sqlite_pool.values()):
        try:
            conn.close()
        except Exception:
            pass
    db_mod._sqlite_pool.clear()
    db_mod._sqlite_pool.update(old_pool)


@pytest.fixture()
def client(patch_data_dir):
    """TestClient for FastAPI with isolated data."""
    from app.api.app import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def admin_client(client, patch_data_dir):
    """Client authenticated as admin user."""
    from app.auth.auth import create_token, get_user_by_username, register_user
    from app.config.data import DB_FILE
    from app.storage.db import PH, close_db, open_db

    if not get_user_by_username("testadmin"):
        register_user("testadmin", "pass1234", email="testadmin@example.com")

    # Promote to admin via DB
    conn = open_db(DB_FILE)
    try:
        conn.cursor().execute(
            f"UPDATE users SET role = {PH} WHERE username = {PH}",
            ("admin", "testadmin"),
        )
        conn.commit()
    finally:
        close_db(conn)

    token = create_token("testadmin")
    client.cookies.set("ga_token", token)
    return client


@pytest.fixture()
def reset_rate_limiter():
    """Clear rate limiters between tests to avoid interference."""
    from app.api.routes.auth import _rate_data as auth_rate
    auth_rate.clear()
    yield
    auth_rate.clear()
