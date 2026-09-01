"""Algoritmo puro y determinista de fragmentación de Knowledge."""

from __future__ import annotations

from typing import Any

CHUNK_CHARS = 4_000
CHUNK_OVERLAP_CHARS = 400


def split_knowledge_text(text: str) -> list[str]:
    """Divide por límites de párrafo y conserva 400 caracteres de contexto."""

    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []
    chunks: list[str] = []
    start = 0
    total = len(normalized)
    while start < total:
        hard_end = min(total, start + CHUNK_CHARS)
        end = hard_end
        if hard_end < total:
            paragraph_end = normalized.rfind("\n\n", start + 1, hard_end)
            # Un salto al principio de la ventana no puede ser el corte: con
            # solapamiento volvería a encontrarse una y otra vez y produciría
            # fragmentos minúsculos. Se respeta párrafo en la mitad útil final.
            if paragraph_end >= start + (CHUNK_CHARS // 2):
                end = paragraph_end
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= total:
            break
        next_start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
        while next_start < end and normalized[next_start].isspace():
            next_start += 1
        start = next_start
    return chunks


def chunk_rows(knowledge_id: str, title: str, content: str) -> list[tuple[Any, ...]]:
    return [
        (f"{knowledge_id}:{index}", knowledge_id, index, title, chunk)
        for index, chunk in enumerate(split_knowledge_text(content))
    ]
