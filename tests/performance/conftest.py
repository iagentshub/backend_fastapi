"""Overrides del conftest global para tests de rendimiento.

El fixture autouse patch_data_dir del conftest raíz resetea la BD en cada
test. Los tests de rendimiento usan scope=module para compartir estado,
así que necesitamos un fixture con el mismo nombre que no resetee entre tests.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def patch_data_dir(perf_data_dir, monkeypatch):  # type: ignore[override]
    """Redirige el entorno al directorio de rendimiento sin resetear entre tests."""
    import app.config.data as cfg
    import app.storage.db as db_mod

    db_file = perf_data_dir / "perf.db"

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("GAIA_DATA_DIR", str(perf_data_dir))

    monkeypatch.setattr(cfg, "DATA_DIR", perf_data_dir)
    monkeypatch.setattr(cfg, "DB_FILE", db_file)
    monkeypatch.setattr(cfg, "CONN_FILE", perf_data_dir / "connections" / "connections.json")
    monkeypatch.setattr(cfg, "AGENTS_DIR", perf_data_dir / "agents")
    monkeypatch.setattr(cfg, "SKILLS_DIR", perf_data_dir / "skills")
    monkeypatch.setattr(cfg, "MEMORY_DIR", perf_data_dir / "memory")
    monkeypatch.setattr(cfg, "SETTINGS_FILE", perf_data_dir / "settings.json")

    import app.auth.auth as auth_mod
    monkeypatch.setattr(auth_mod, "SETTINGS_FILE", perf_data_dir / "settings.json")
    monkeypatch.setattr(auth_mod, "DB_FILE", db_file)

    monkeypatch.setattr(db_mod, "IS_PG", False)
    monkeypatch.setattr(db_mod, "PH", "?")

    yield
