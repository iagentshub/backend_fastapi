"""Fragmentación determinista y recuperación textual de Knowledge."""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.sql import sql
from app.storage import db as storage_db
from app.storage.db import AsyncConn, open_db
from app.storage.knowledge_chunking import chunk_rows

SEARCH_LIMIT = 8
FALLBACK_DOCUMENT_LIMIT = 4
_CANDIDATE_BATCH = 400
_TOKEN_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)


async def replace_knowledge_chunks(
    conn: AsyncConn,
    *,
    knowledge_id: str,
    title: str,
    content: str,
) -> None:
    """Reemplaza todos los fragmentos dentro de la transacción del documento."""

    await conn.execute(
        sql("queries/knowledge_chunks:delete_by_knowledge"), (knowledge_id,)
    )
    rows = chunk_rows(knowledge_id, title, content)
    if rows:
        await conn.executemany(
            sql("queries/knowledge_chunks:insert_chunk"),
            rows,
        )


async def update_knowledge_chunk_title(
    conn: AsyncConn, *, knowledge_id: str, title: str
) -> None:
    await conn.execute(
        sql("queries/knowledge_chunks:update_title"),
        (title, knowledge_id),
    )


def _batches(values: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(values), _CANDIDATE_BATCH):
        yield values[start : start + _CANDIDATE_BATCH]


def _search_tokens(query: str) -> list[str]:
    return list(dict.fromkeys(token.lower() for token in _TOKEN_RE.findall(query)))


def _sqlite_fts_query(tokens: Iterable[str]) -> str:
    # Nunca se pasa la sintaxis FTS escrita por el usuario. Citar tokens evita
    # operadores, comillas o paréntesis inválidos.
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


async def search_knowledge_chunks(
    query: str,
    knowledge_ids: Iterable[str],
    *,
    limit: int = SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    """Busca solo dentro del conjunto de documentos enlazados por el llamador."""

    candidate_ids = list(dict.fromkeys(str(value) for value in knowledge_ids if value))
    if not candidate_ids or limit <= 0:
        return []
    tokens = _search_tokens(query)
    if not tokens:
        return []
    sqlite_query = _sqlite_fts_query(tokens)
    pg_query = " OR ".join(tokens)

    matches: list[dict[str, Any]] = []
    async with open_db() as conn:
        for batch in _batches(candidate_ids):
            placeholders = ",".join("?" for _ in batch)
            if storage_db.IS_PG:
                rows = await conn.fetchall(
                    "SELECT c.knowledge_id,c.chunk_index,c.title,c.content,"
                    "ts_rank_cd(c.search_vector, websearch_to_tsquery('simple', ?)) AS rank "
                    "FROM knowledge_chunks c JOIN knowledge_items k ON k.id=c.knowledge_id "
                    "WHERE c.knowledge_id IN ("
                    f"{placeholders}) AND k.is_active=1 "
                    "AND c.search_vector @@ websearch_to_tsquery('simple', ?) "
                    "ORDER BY rank DESC,c.knowledge_id,c.chunk_index LIMIT ?",
                    (pg_query, *batch, pg_query, limit),
                )
            else:
                rows = await conn.fetchall(
                    "SELECT c.knowledge_id,c.chunk_index,c.title,c.content,"
                    "-bm25(knowledge_chunks_fts) AS rank "
                    "FROM knowledge_chunks_fts "
                    "JOIN knowledge_chunks c ON c.rowid=knowledge_chunks_fts.rowid "
                    "JOIN knowledge_items k ON k.id=c.knowledge_id "
                    "WHERE knowledge_chunks_fts MATCH ? AND c.knowledge_id IN ("
                    f"{placeholders}) AND k.is_active=1 "
                    "ORDER BY bm25(knowledge_chunks_fts),c.knowledge_id,c.chunk_index LIMIT ?",
                    (sqlite_query, *batch, limit),
                )
            matches.extend(dict(row) for row in rows)
    matches.sort(
        key=lambda row: (
            -float(row.get("rank") or 0),
            str(row.get("knowledge_id") or ""),
            int(row.get("chunk_index") or 0),
        )
    )
    return matches[:limit]


async def fallback_knowledge_chunks(
    knowledge_ids: Iterable[str], *, limit: int = FALLBACK_DOCUMENT_LIMIT
) -> list[dict[str, Any]]:
    """Devuelve el primer fragmento de hasta cuatro documentos enlazados."""

    ordered_ids = list(dict.fromkeys(str(value) for value in knowledge_ids if value))
    if not ordered_ids or limit <= 0:
        return []
    selected = ordered_ids[:limit]
    placeholders = ",".join("?" for _ in selected)
    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT c.knowledge_id,c.chunk_index,c.title,c.content,0 AS rank "
            "FROM knowledge_chunks c JOIN knowledge_items k ON k.id=c.knowledge_id "
            f"WHERE c.knowledge_id IN ({placeholders}) AND c.chunk_index=0 "
            "AND k.is_active=1",
            tuple(selected),
        )
    by_id = {str(row["knowledge_id"]): dict(row) for row in rows}
    return [by_id[item_id] for item_id in selected if item_id in by_id]
