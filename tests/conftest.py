"""fixtures compartidos de tests."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ── directorio de datos temporal ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def tmp_data_dir():
    """Crea un directorio de datos aislado para todos los tests."""
    d = Path(tempfile.mkdtemp(prefix="gaia_test_"))
    # estructura mínima
    (d / "connections").mkdir()
    (d / "agents").mkdir()
    (d / "skills").mkdir()
    (d / "memory").mkdir()
    (d / "settings.json").write_text(
        json.dumps({
            "admin_username": "admin",
            "admin_password_hash": "$2b$12$pFGz5cGeUIzDdGYtc8dXee2l1iWWkGVga2L3pZGLVpdWkPNO/oTfS",  # "admin"
            "jwt_secret": "test-secret-key-for-tests-only",
        }),
        encoding="utf-8",
    )
    (d / "connections" / "connections.json").write_text("[]", encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def patch_data_dir(tmp_data_dir, monkeypatch):
    """Redirige GAIA_DATA_DIR al directorio temporal antes de cada test."""
    # Reinicia users.json para aislamiento entre tests
    (tmp_data_dir / "users.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_data_dir))
    # Forzar que los módulos de config usen el valor actualizado
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
def admin_client(client):
    """Client ya autenticado como admin."""
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    return client


@pytest.fixture()
def reset_rate_limiter():
    """Limpia el rate limiter entre tests para evitar interferencias."""
    from app.api.routes.auth import _rate_data
    _rate_data.clear()
    yield
    _rate_data.clear()
