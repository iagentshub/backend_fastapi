"""Evita que el DDL acumule tablas sin consumidores en la aplicación."""

from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?"
    r"([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
INFRASTRUCTURE_TABLES = {"schema_migrations"}


def _declared_tables() -> set[str]:
    sources = [*(APP_ROOT / "sql" / "schema").glob("*.sql")]
    sources.extend((APP_ROOT / "storage" / "migrations").glob("*.py"))
    return {
        match.lower()
        for source in sources
        for match in TABLE_PATTERN.findall(source.read_text(encoding="utf-8"))
    }


def _runtime_sources() -> str:
    """Todo lo que ejecuta la aplicación: el Python y las consultas en fichero.

    Las consultas viven en `app/sql/queries/` desde que el SQL estático salió
    de los módulos, así que buscar la tabla solo en los `.py` daba por muerta
    cualquiera cuyo único consumidor fuera un `.sql`.
    """
    fuentes = [
        source
        for source in APP_ROOT.rglob("*.py")
        if "migrations" not in source.parts
    ]
    fuentes.extend((APP_ROOT / "sql" / "queries").rglob("*.sql"))
    return "\n".join(source.read_text(encoding="utf-8") for source in fuentes)


def test_every_declared_application_table_has_a_runtime_consumer():
    runtime = _runtime_sources()
    unused = {
        table
        for table in _declared_tables() - INFRASTRUCTURE_TABLES
        if re.search(rf"\b{re.escape(table)}\b", runtime) is None
    }

    assert unused == set(), (
        "Tablas declaradas sin consumidores activos. Elimina el DDL o documenta "
        f"su función de infraestructura: {sorted(unused)}"
    )


def test_obsolete_pack_membership_table_is_not_declared():
    assert "knowledge_pack_items" not in _declared_tables()
