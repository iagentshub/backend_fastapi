"""Fixtures compartidos de tests."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def tmp_data_dir():
    """Crea un directorio de datos aislado para todos los tests."""
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
def patch_data_dir(tmp_data_dir, monkeypatch):
    """Redirige GAIA_DATA_DIR al directorio temporal antes de cada test."""
    (tmp_data_dir / "users.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_data_dir))

    import app.config.data as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_data_dir)
    monkeypatch.setattr(cfg, "CONN_FILE", tmp_data_dir / "connections" / "connections.json")
    monkeypatch.setattr(cfg, "AGENTS_DIR", tmp_data_dir / "agents")
    monkeypatch.setattr(cfg, "SKILLS_DIR", tmp_data_dir / "skills")
    monkeypatch.setattr(cfg, "MEMORY_DIR", tmp_data_dir / "memory")
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_data_dir / "settings.json")

    import app.auth.auth as auth_mod
    monkeypatch.setattr(auth_mod, "SETTINGS_FILE", tmp_data_dir / "settings.json")
    monkeypatch.setattr(auth_mod, "_USERS_PATH", tmp_data_dir / "users.json")


@pytest.fixture()
def client(patch_data_dir):
    """TestClient de FastAPI con datos aislados."""
    from app.api.app import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def admin_client(client, tmp_data_dir):
    """Client autenticado como usuario con rol admin."""
    import json as _json
    from app.auth.auth import register_user, create_token
    register_user("testadmin", "pass1234")
    users_path = tmp_data_dir / "users.json"
    users = _json.loads(users_path.read_text())
    for u in users:
        if u["username"] == "testadmin":
            u["role"] = "admin"
    users_path.write_text(_json.dumps(users))
    token = create_token("testadmin")
    client.cookies.set("ga_token", token)
    return client


@pytest.fixture()
def reset_rate_limiter():
    """Limpia los rate limiters entre tests para evitar interferencias."""
    from app.api.routes.auth import _rate_data as auth_rate
    auth_rate.clear()
    yield
    auth_rate.clear()
