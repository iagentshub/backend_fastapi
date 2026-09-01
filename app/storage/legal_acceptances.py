"""Persistencia append-only de consentimientos legales versionados."""

from __future__ import annotations

from typing import Iterable

from app.sql import sql
from app.storage import db as _db
from app.storage.db import AsyncConn, open_db
from app.utils import now_iso
from app.utils.generators import generate_id


class LegalAcceptanceStorage:
    async def record(
        self,
        user_id: str,
        documents: Iterable[dict[str, str]],
        *,
        source: str,
        conn: AsyncConn | None = None,
    ) -> None:
        async def write(target: AsyncConn) -> None:
            accepted_at = now_iso()
            query = (
                "queries/legal_acceptances:insert_pg"
                if _db.IS_PG
                else "queries/legal_acceptances:insert_sqlite"
            )
            for document in documents:
                await target.execute(
                    sql(query),
                    (
                        generate_id(32),
                        user_id,
                        document["document_type"],
                        document["version"],
                        document["locale"],
                        document["content_sha256"],
                        document["document_url"],
                        accepted_at,
                        source,
                    ),
                )

        if conn is not None:
            await write(conn)
            return
        async with open_db() as own_conn, own_conn.transaction(immediate=True):
            await write(own_conn)

    async def has_current(
        self, user_id: str, documents: Iterable[dict[str, str]]
    ) -> bool:
        async with open_db() as conn:
            for document in documents:
                if not await conn.fetchone(
                    sql("queries/legal_acceptances:has_current"),
                    (
                        user_id,
                        document["document_type"],
                        document["version"],
                    ),
                ):
                    return False
        return True
