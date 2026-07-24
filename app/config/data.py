"""Rutas de los directorios y ficheros de datos."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).parents[2]

# En Docker: GAIA_DATA_DIR=/data (montado desde el host).
# En local sin variable: sube al directorio padre para reutilizar iAgents/data —
# el repo que centraliza los despliegues del proyecto — en vez de crear una carpeta
# de datos nueva y separada. Los tests SIEMPRE sobrescriben GAIA_DATA_DIR con un
# directorio temporal aislado (ver tests/conftest.py), así que esto nunca afecta
# a los tests, solo al servidor de desarrollo arrancado a mano.
_default_data = BASE_DIR.parent / "iAgents" / "data"
DATA_DIR      = Path(os.getenv("GAIA_DATA_DIR", str(_default_data)))
DB_FILE       = DATA_DIR / "hub.db"
AGENTS_DIR    = DATA_DIR / "agents"
SKILLS_DIR    = DATA_DIR / "skills"
MEMORY_DIR    = DATA_DIR / "memory"
SETTINGS_FILE = DATA_DIR / "settings.json"
CENTINEL_STATE_FILE = DATA_DIR / "centinel_state.json"
# Legacy paths — referenced only by storage migration helpers
CONN_FILE     = DATA_DIR / "connections" / "connections.json"
ACCOUNTS_DIR  = DATA_DIR / "accounts"
