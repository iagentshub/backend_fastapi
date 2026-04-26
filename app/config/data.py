"""Rutas de los directorios y ficheros de datos."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).parents[2]

DATA_DIR      = Path(os.getenv("GAIA_DATA_DIR", str(BASE_DIR / "data")))
CONN_FILE     = DATA_DIR / "connections" / "connections.json"
AGENTS_DIR    = DATA_DIR / "agents"
SKILLS_DIR    = DATA_DIR / "skills"
MEMORY_DIR    = DATA_DIR / "memory"
SETTINGS_FILE = DATA_DIR / "settings.json"
