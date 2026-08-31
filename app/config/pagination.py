"""Límites operativos del contrato de paginación cursor."""

from __future__ import annotations

import os

CURSOR_TTL_SECONDS = max(
    60, min(int(os.getenv("GAIA_CURSOR_TTL_SECONDS", "3600")), 86_400)
)
EXACT_TOTAL_TIMEOUT_SECONDS = max(
    0.05,
    min(float(os.getenv("GAIA_PAGINATION_TOTAL_TIMEOUT_SECONDS", "2.0")), 30.0),
)
EXACT_TOTAL_MAX_CONCURRENCY = max(
    1,
    min(int(os.getenv("GAIA_PAGINATION_TOTAL_MAX_CONCURRENCY", "1")), 8),
)
