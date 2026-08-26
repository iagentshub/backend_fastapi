"""Límites estructurales de importación, independientes del tamaño en bytes."""

from __future__ import annotations

import os


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# No sustituyen a max_request_bytes. Protegen el parser multipart y la
# normalización incluso cuando el administrador permite tamaños sin límite.
DIRECTORY_IMPORT_MAX_FILES = _positive_int("GAIA_DIRECTORY_IMPORT_MAX_FILES", 5000)
DIRECTORY_IMPORT_MAX_DEPTH = _positive_int("GAIA_DIRECTORY_IMPORT_MAX_DEPTH", 32)
DIRECTORY_IMPORT_MAX_PATH_LENGTH = _positive_int(
    "GAIA_DIRECTORY_IMPORT_MAX_PATH_LENGTH", 500
)
