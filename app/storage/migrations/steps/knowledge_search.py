"""SEARCH-017: fragmentos y búsqueda FTS de Knowledge."""

from __future__ import annotations

from typing import Any

from app.storage.knowledge_chunking import chunk_rows

_SQLITE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    title,
    content,
    content='knowledge_chunks',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
)
"""


async def _knowledge_search_sqlite(conn: Any) -> None:
    await conn.execute(_SQLITE_FTS)
    await conn.execute(
        "CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_ai AFTER INSERT ON knowledge_chunks BEGIN "
        "INSERT INTO knowledge_chunks_fts(rowid,title,content) "
        "VALUES (new.rowid,new.title,new.content); END"
    )
    await conn.execute(
        "CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_ad AFTER DELETE ON knowledge_chunks BEGIN "
        "INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts,rowid,title,content) "
        "VALUES ('delete',old.rowid,old.title,old.content); END"
    )
    await conn.execute(
        "CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_au AFTER UPDATE ON knowledge_chunks BEGIN "
        "INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts,rowid,title,content) "
        "VALUES ('delete',old.rowid,old.title,old.content); "
        "INSERT INTO knowledge_chunks_fts(rowid,title,content) "
        "VALUES (new.rowid,new.title,new.content); END"
    )
    await conn.execute("DELETE FROM knowledge_chunks")
    cursor = await conn.execute("SELECT id FROM knowledge_items ORDER BY id")
    document_ids = await cursor.fetchall()
    for (knowledge_id,) in document_ids:
        cursor = await conn.execute(
            "SELECT title,content FROM knowledge_items WHERE id=?", (knowledge_id,)
        )
        document = await cursor.fetchone()
        if document is None:  # pragma: no cover - la migración tiene un escritor
            continue
        rows = chunk_rows(str(knowledge_id), str(document[0]), str(document[1]))
        if rows:
            await conn.executemany(
                "INSERT INTO knowledge_chunks "
                "(id,knowledge_id,chunk_index,title,content) VALUES (?,?,?,?,?)",
                rows,
            )
    await conn.execute(
        "INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts) VALUES ('rebuild')"
    )


async def _knowledge_search_pg(conn: Any) -> None:
    await conn.execute("DELETE FROM knowledge_chunks")
    document_ids = await conn.fetch("SELECT id FROM knowledge_items ORDER BY id")
    for (knowledge_id,) in document_ids:
        document = await conn.fetchrow(
            "SELECT title,content FROM knowledge_items WHERE id=$1", knowledge_id
        )
        if document is None:  # pragma: no cover - la migración tiene un escritor
            continue
        rows = chunk_rows(str(knowledge_id), str(document[0]), str(document[1]))
        if rows:
            await conn.executemany(
                "INSERT INTO knowledge_chunks "
                "(id,knowledge_id,chunk_index,title,content) VALUES ($1,$2,$3,$4,$5)",
                rows,
            )
