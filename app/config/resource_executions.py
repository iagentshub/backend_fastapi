"""Política central de leases distribuidos de agentes y workflows."""

from __future__ import annotations

import os


def _seconds(name: str, default: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


RESOURCE_EXECUTION_HEARTBEAT_SECONDS = _seconds(
    "GAIA_RESOURCE_EXECUTION_HEARTBEAT_SECONDS", 10, minimum=5
)
RESOURCE_EXECUTION_STALE_SECONDS = max(
    RESOURCE_EXECUTION_HEARTBEAT_SECONDS * 3,
    _seconds("GAIA_RESOURCE_EXECUTION_STALE_SECONDS", 300, minimum=30),
)

