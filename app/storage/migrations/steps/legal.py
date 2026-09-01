"""Tabla append-only de aceptaciones legales demostrables."""

from __future__ import annotations

from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS legal_acceptances (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_type TEXT NOT NULL CHECK (document_type IN ('terms', 'privacy')),
    version TEXT NOT NULL,
    locale TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    document_url TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('registration', 'in_session')),
    UNIQUE (user_id, document_type, version)
)
"""


async def _legal_acceptances_sqlite(conn: Any) -> None:
    # El esquema canónico crea una tabla provisional sin FK: al abrir una BD
    # legacy, `users.id` aún puede no ser UNIQUE y SQLite rechazaría cualquier
    # escritura por "foreign key mismatch" antes de que el catch-up la repare.
    # La 46 corre después del catch-up y puede instalar ya la FK definitiva.
    await conn.execute("DROP TABLE IF EXISTS legal_acceptances")
    await conn.execute(_DDL)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_legal_acceptances_current "
        "ON legal_acceptances(user_id, document_type, version)"
    )


async def _legal_acceptances_pg(conn: Any) -> None:
    await conn.execute("DROP TABLE IF EXISTS legal_acceptances")
    await conn.execute(_DDL)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_legal_acceptances_current "
        "ON legal_acceptances(user_id, document_type, version)"
    )
