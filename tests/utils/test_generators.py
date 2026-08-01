"""Tests de las funciones de control de app/utils/generators.py.

Incluye el test guardián: ids y fechas de datos se generan SOLO a través de
generate_id()/generate_date(), nunca inline.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.utils.generators import generate_date, generate_id

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def test_generate_id_default_length():
    assert re.fullmatch(r"[0-9a-f]{12}", generate_id())


def test_generate_id_custom_length():
    assert re.fullmatch(r"[0-9a-f]{16}", generate_id(16))
    assert re.fullmatch(r"[0-9a-f]{32}", generate_id(32))


def test_generate_id_unique():
    ids = {generate_id() for _ in range(200)}
    assert len(ids) == 200


def test_generate_date_iso_utc():
    value = generate_date()
    assert value.endswith("+00:00")
    assert "T" in value


def test_guard_no_inline_uuid_generation():
    """Nadie genera ids inline: uuid4().hex solo puede vivir en generators.py."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if path.name == "generators.py":
            continue
        if "uuid4().hex" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(APP_DIR)))
    assert not offenders, f"Generación de id inline (usa generate_id): {offenders}"


def test_guard_no_local_now_helpers():
    """Nadie redefine su propio helper de fecha: usa generate_date/now_iso."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if path.name in ("generators.py", "__init__.py"):
            continue
        if "def _now(" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(APP_DIR)))
    assert not offenders, f"Helper de fecha duplicado (usa generate_date): {offenders}"
