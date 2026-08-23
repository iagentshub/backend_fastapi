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
EXTERNAL_QUERY_TABLES = {
    "dbstat",
    "information_schema",
    "pg_stat_user_tables",
    "set",  # `DO UPDATE SET`, no una tabla.
    "sqlite_master",
}
QUERY_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO|DELETE\s+FROM)\s+[\"`]?"
    r"([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


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


def test_every_query_table_is_declared_in_the_canonical_schema():
    """Una consulta activa nunca debe depender de DDL escondido en migraciones."""
    declared = {source.stem for source in (APP_ROOT / "sql" / "schema").glob("*.sql")}
    referenced = {
        table.lower()
        for source in (APP_ROOT / "sql" / "queries").rglob("*.sql")
        for table in QUERY_TABLE_PATTERN.findall(source.read_text(encoding="utf-8"))
    }
    missing = referenced - declared - EXTERNAL_QUERY_TABLES
    assert missing == set(), (
        "Tablas consultadas fuera del esquema canónico: "
        f"{sorted(missing)}. Añade su fichero a app/sql/schema y a TABLAS."
    )


def test_obsolete_pack_membership_table_is_not_declared():
    assert "knowledge_pack_items" not in _declared_tables()


def test_social_schema_is_not_redeclared_in_legacy_catchup():
    social_tables = {
        "resource_group_shares",
        "resource_labels",
        "resource_social",
        "resource_stars",
        "user_follows",
    }
    legacy_sources = (APP_ROOT / "storage" / "migrations" / "legacy").glob(
        "_catchup_*.py"
    )
    redeclared = {
        table.lower()
        for source in legacy_sources
        for table in TABLE_PATTERN.findall(source.read_text(encoding="utf-8"))
    }
    assert redeclared.isdisjoint(social_tables), (
        "El DDL social debe vivir solo en app/sql/schema: "
        f"{sorted(redeclared & social_tables)}"
    )
