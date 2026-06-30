"""Shared test fixtures."""
from __future__ import annotations

import asyncio
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
    db_file = tmp_path / "hub.db"
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_data_dir))

    # Patch config module attrs — _cfg.DB_FILE is read dynamically by app lifespan
    import app.config.data as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_data_dir)
    monkeypatch.setattr(cfg, "DB_FILE", db_file)
    monkeypatch.setattr(cfg, "CONN_FILE", tmp_data_dir / "connections" / "connections.json")
    monkeypatch.setattr(cfg, "AGENTS_DIR", tmp_data_dir / "agents")
    monkeypatch.setattr(cfg, "SKILLS_DIR", tmp_data_dir / "skills")
    monkeypatch.setattr(cfg, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_data_dir / "settings.json")

    # Force SQLite mode and initialize the per-test DB BEFORE importing routes
    import app.storage.db as db_mod
    monkeypatch.setattr(db_mod, "IS_PG", False)
    monkeypatch.setattr(db_mod, "PH", "?")

    old_sqlite_path = db_mod._sqlite_path
    old_pg_pool = db_mod._pg_pool

    asyncio.run(db_mod.init_db(db_file))

    # Patch auth module paths
    import app.auth.auth as auth_mod
    monkeypatch.setattr(auth_mod, "SETTINGS_FILE", tmp_data_dir / "settings.json")

    # Patch MemoryStorage live instances
    from app.storage.storage import MemoryStorage
    isolated_memory = MemoryStorage(memory_dir)
    import app.api.routes.memory as memory_routes
    import app.api.routes.agents as agents_routes
    monkeypatch.setattr(memory_routes, "_storage", isolated_memory)
    monkeypatch.setattr(agents_routes, "_memory", isolated_memory)

    yield

    # Reset DB state after test
    asyncio.run(db_mod.close_db_pool())
    db_mod._sqlite_path = old_sqlite_path
    db_mod._pg_pool = old_pg_pool


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
    from app.storage.db import open_db

    async def _setup():
        if not await get_user_by_username("testadmin"):
            await register_user("testadmin", "pass1234", email="testadmin@example.com")
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                ("admin", "testadmin"),
            )
            await conn.commit()

    asyncio.run(_setup())
    token = create_token("testadmin")
    client.cookies.set("ga_token", token)
    return client


@pytest.fixture()
def reset_rate_limiter():
    """Clear rate limiters between tests to avoid interference."""
    from app.api.routes.auth import _register_limiter
    _register_limiter._data.clear()
    yield
    _register_limiter._data.clear()
