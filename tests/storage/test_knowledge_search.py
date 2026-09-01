"""SEARCH-017: fragmentación, sincronización y recuperación FTS acotada."""

from __future__ import annotations

import hashlib

import aiosqlite

from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_chunking import (
    CHUNK_CHARS,
    CHUNK_OVERLAP_CHARS,
    split_knowledge_text,
)
from app.storage.knowledge_chunks import (
    fallback_knowledge_chunks,
    search_knowledge_chunks,
)
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.migrations.steps.knowledge_search import _knowledge_search_sqlite
from app.storage.schema import tabla_ddl


def test_fragmenta_de_forma_determinista_con_cota_y_solapamiento():
    text = "A" * 2_600 + "\n\n" + "B" * 2_600 + "\n\n" + "C" * 2_600

    first = split_knowledge_text(text)
    second = split_knowledge_text(text.replace("\n", "\r\n"))

    assert first == second
    assert len(first) >= 3
    assert all(0 < len(chunk) <= CHUNK_CHARS for chunk in first)
    assert first[0][-CHUNK_OVERLAP_CHARS:].strip() in first[1]


async def test_save_update_title_deactivate_and_delete_keep_fts_in_sync():
    storage = KnowledgeStorage()
    item = await storage.save(
        type="document",
        title="Manual orbital",
        source="manual.md",
        content="La calibración usa el término ASTROLABIO solamente aquí.",
        owner_id="owner-search",
    )

    matches = await search_knowledge_chunks("astrolabio", [item["id"]])
    assert [match["knowledge_id"] for match in matches] == [item["id"]]
    assert matches[0]["title"] == "Manual orbital"

    await storage.save(
        type="document",
        title="Manual náutico",
        source="manual.md",
        content="La navegación usa una BRÚJULA de respaldo.",
        owner_id="owner-search",
        item_id=item["id"],
    )
    assert await search_knowledge_chunks("astrolabio", [item["id"]]) == []
    matches = await search_knowledge_chunks("brujula", [item["id"]])
    assert matches[0]["title"] == "Manual náutico"

    assert await storage.update_metadata(
        item["id"], "owner-search", title="Guía marítima", labels=["private"]
    )
    matches = await search_knowledge_chunks("brujula", [item["id"]])
    assert matches[0]["title"] == "Guía marítima"

    assert await storage.set_active(item["id"], "owner-search", False)
    assert await search_knowledge_chunks("brujula", [item["id"]]) == []
    assert await storage.set_active(item["id"], "owner-search", True)

    assert await storage.delete(item["id"], "owner-search")
    assert await search_knowledge_chunks("brujula", [item["id"]]) == []
    async with open_db() as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE knowledge_id=?",
                (item["id"],),
            )
            == 0
        )


async def test_busqueda_no_sale_de_ids_vinculados_y_fallback_respeta_orden():
    storage = KnowledgeStorage()
    allowed = await storage.save(
        type="text",
        title="Permitido",
        source="",
        content="Procedimiento de despliegue azul.",
        owner_id="owner-search",
    )
    hidden = await storage.save(
        type="text",
        title="No vinculado",
        source="",
        content="El secreto AZUL ULTRAMAR aparece muchas veces: azul ultramar azul.",
        owner_id="other-owner",
    )

    matches = await search_knowledge_chunks("azul ultramar", [allowed["id"]])
    assert {match["knowledge_id"] for match in matches} <= {allowed["id"]}
    assert hidden["id"] not in {match["knowledge_id"] for match in matches}

    fallback = await fallback_knowledge_chunks([hidden["id"], allowed["id"]], limit=1)
    assert [item["knowledge_id"] for item in fallback] == [hidden["id"]]


async def test_pack_create_replace_and_delete_sync_chunks_atomically():
    storage = KnowledgePackStorage()

    def pack_item(content: str) -> dict:
        return {
            "relative_path": "docs/manual.md",
            "content": content,
            "kind": "document",
            "mime_type": "text/markdown",
            "size_bytes": len(content.encode()),
            "checksum": hashlib.sha256(content.encode()).hexdigest(),
        }

    pack = await storage.create(
        owner_id="pack-owner",
        name="Manuales",
        description="",
        labels=["private"],
        items=[pack_item("La clave inicial es COBALTO.")],
    )
    item_id = pack["items"][0]["id"]
    assert await search_knowledge_chunks("cobalto", [item_id])

    replacement = pack_item("La clave vigente es MAGNESIO.")
    result = await storage.replace_items(pack["id"], "pack-owner", [replacement])
    assert result == {"added": 0, "updated": 1, "removed": 0, "total": 1}
    assert await search_knowledge_chunks("cobalto", [item_id]) == []
    assert await search_knowledge_chunks("magnesio", [item_id])

    assert await storage.delete(pack["id"], "pack-owner")
    assert await search_knowledge_chunks("magnesio", [item_id]) == []


async def test_migration_47_backfills_existing_knowledge_and_builds_fts(tmp_path):
    async with aiosqlite.connect(tmp_path / "knowledge-search-backfill.db") as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript(
            "CREATE TABLE knowledge_items ("
            "id TEXT PRIMARY KEY,title TEXT NOT NULL,content TEXT NOT NULL);"
            + tabla_ddl("knowledge_chunks", "sqlite")
        )
        await conn.execute(
            "INSERT INTO knowledge_items VALUES (?,?,?)",
            ("legacy-doc", "Documento legacy", "Contiene la palabra HELIOTROPO."),
        )

        await _knowledge_search_sqlite(conn)
        chunks = await conn.execute_fetchall(
            "SELECT knowledge_id,chunk_index,content FROM knowledge_chunks"
        )
        matches = await conn.execute_fetchall(
            "SELECT rowid FROM knowledge_chunks_fts "
            "WHERE knowledge_chunks_fts MATCH 'heliotropo'"
        )

    assert chunks == [("legacy-doc", 0, "Contiene la palabra HELIOTROPO.")]
    assert len(matches) == 1
