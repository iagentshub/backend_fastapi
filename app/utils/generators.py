"""Funciones de control centralizadas para generar valores de sistema.

Todo id y toda fecha de datos se generan aquí — nunca inline con
`uuid4().hex[:n]` o `datetime.now(...)` sueltos por el código. Un test
guardián vigila que no reaparezcan usos inline.
"""

from __future__ import annotations

from uuid import uuid4

from app.utils import now_iso

DEFAULT_ID_LENGTH = 12


def generate_id(length: int = DEFAULT_ID_LENGTH) -> str:
    """Id alfanumérico único e independiente del nombre del objeto."""
    return uuid4().hex[:length]


def generate_date() -> str:
    """Fecha-hora actual en ISO-8601 UTC, formato único de todo el sistema."""
    return now_iso()
