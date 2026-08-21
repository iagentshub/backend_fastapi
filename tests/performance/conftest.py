"""Overrides del conftest global para tests de rendimiento.

El fixture autouse patch_data_dir del conftest raíz resetea la BD en cada
test. Los tests de rendimiento usan scope=module para compartir estado,
así que necesitamos un fixture con el mismo nombre que no resetee entre tests.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def perf_data_dir():
    """Directorio aislado compartido por cada módulo de rendimiento."""
    directory = Path(tempfile.mkdtemp(prefix="gaia_perf_"))
    for name in ("connections", "agents", "skills", "memory"):
        (directory / name).mkdir()
    (directory / "settings.json").write_text(
        json.dumps({"jwt_secret": "perf-test-secret"}),
        encoding="utf-8",
    )
    (directory / "connections" / "connections.json").write_text("[]", encoding="utf-8")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(autouse=True)
def patch_data_dir(perf_data_dir, monkeypatch):  # type: ignore[override]
    """Redirige el entorno al directorio de rendimiento sin resetear entre tests."""
    import app.config.data as cfg
    import app.config.database as database_cfg
    import app.storage.db as db_mod

    db_file = perf_data_dir / "perf.db"

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("GAIA_DATA_DIR", str(perf_data_dir))

    monkeypatch.setattr(cfg, "DATA_DIR", perf_data_dir)
    monkeypatch.setattr(database_cfg, "DB_FILE", db_file)
    monkeypatch.setattr(
        cfg, "CONN_FILE", perf_data_dir / "connections" / "connections.json"
    )
    monkeypatch.setattr(cfg, "AGENTS_DIR", perf_data_dir / "agents")
    monkeypatch.setattr(cfg, "SKILLS_DIR", perf_data_dir / "skills")
    monkeypatch.setattr(cfg, "MEMORY_DIR", perf_data_dir / "memory")
    monkeypatch.setattr(cfg, "SETTINGS_FILE", perf_data_dir / "settings.json")

    import app.auth.passwords as passwords_mod

    monkeypatch.setattr(passwords_mod, "SETTINGS_FILE", perf_data_dir / "settings.json")

    monkeypatch.setattr(db_mod, "IS_PG", False)
    monkeypatch.setattr(db_mod, "PH", "?")

    # El estado de la BD se comparte en disco, pero pytest crea un event loop
    # por test. asyncio.Queue se liga al loop cuando hay espera por saturación,
    # así que cada test necesita su pool nuevo igual que cada worker real.
    asyncio.run(db_mod.close_db_pool())
    asyncio.run(db_mod.init_db(db_file))

    yield

    asyncio.run(db_mod.close_db_pool())
