"""Validadores compartidos por rutas y servicios."""

from __future__ import annotations

import re

_EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def is_valid_email(value: str) -> bool:
    """Valida el formato común usado por registro y administración."""
    return bool(_EMAIL_PATTERN.fullmatch(value))
