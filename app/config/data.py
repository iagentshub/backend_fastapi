"""Rutas de los directorios y ficheros de datos."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).parents[2]

# En Docker: GAIA_DATA_DIR=/data (montado desde iagentshub/data).
# En local sin variable: sube al directorio padre para apuntar a iagentshub/data.
_default_data = BASE_DIR.parent / "iagentshub" / "data"
DATA_DIR      = Path(os.getenv("GAIA_DATA_DIR", str(_default_data)))
CONN_FILE     = DATA_DIR / "connections" / "connections.json"
AGENTS_DIR    = DATA_DIR / "agents"
SKILLS_DIR    = DATA_DIR / "skills"
MEMORY_DIR    = DATA_DIR / "memory"
SETTINGS_FILE = DATA_DIR / "settings.json"
ACCOUNTS_DIR  = DATA_DIR / "accounts"
