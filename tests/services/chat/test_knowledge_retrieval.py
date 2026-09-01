"""Recuperación acotada de Knowledge antes de invocar al proveedor."""

from __future__ import annotations

import json
from unittest.mock import patch

from app.services.chat import stream_chat
from app.storage.knowledge import KnowledgeStorage
from tests.services.chat._helpers import (
    _make_agent,
    _make_conn,
    _skill_storage,
    _sse_done_response,
)


async def _run(agent, question, ids, *, attached=None):
    payloads: list[dict] = []

    def fake_urlopen(request, timeout):
        payloads.append(json.loads(request.data.decode()))
        return _sse_done_response()

    with patch(
        "app.connections.openai_compatible.safe_urlopen", side_effect=fake_urlopen
    ):
        events = [
            event
            async for event in stream_chat(
                agent,
                _make_conn("openai"),
                [{"role": "user", "content": question}],
                _skill_storage(),
                knowledge_storage=KnowledgeStorage(),
                attached_knowledge=attached,
                resolved_knowledge_ids=ids,
            )
        ]
    return events, payloads[0]["messages"][0]["content"]


async def test_inyecta_solo_fragmentos_relevantes_y_como_maximo_ocho():
    storage = KnowledgeStorage()
    relevant = await storage.save(
        type="document",
        title="Operaciones",
        source="ops.md",
        content=("telemetría orion " * 300 + "\n\n") * 12,
        owner_id="retrieval-owner",
    )
    irrelevant = await storage.save(
        type="document",
        title="Cocina",
        source="cook.md",
        content="Receta de pan y levadura.",
        owner_id="retrieval-owner",
    )

    agent = _make_agent("openai")
    events, system = await _run(
        agent,
        "¿Qué indica la telemetría Orion?",
        [relevant["id"], irrelevant["id"]],
    )

    assert "telemetría orion" in system
    assert "Receta de pan" not in system
    assert system.count("## Conocimiento recuperado:") <= 8
    assert not any(
        '"code": "knowledge_retrieval_fallback"' in event for event in events
    )


async def test_sin_coincidencias_usa_hasta_cuatro_inicios_y_emite_aviso():
    storage = KnowledgeStorage()
    ids = []
    for index in range(6):
        item = await storage.save(
            type="text",
            title=f"Documento {index}",
            source="",
            content=f"Inicio identificable {index} sin la consulta buscada.",
            owner_id="fallback-owner",
        )
        ids.append(item["id"])

    events, system = await _run(_make_agent("openai"), "xqzv inexistente", ids)
    warning = next(
        json.loads(event[6:])
        for event in events
        if '"code": "knowledge_retrieval_fallback"' in event
    )

    assert warning["sources"] == ids[:4]
    assert system.count("## Conocimiento recuperado:") == 4
    assert "Inicio identificable 4" not in system


async def test_adjuntado_conserva_contenido_completo_y_no_se_duplica_en_fts():
    storage = KnowledgeStorage()
    item = await storage.save(
        type="text",
        title="Adjunto",
        source="",
        content="CONTENIDO COMPLETO PRIORITARIO con término saturno.",
        owner_id="attached-owner",
    )
    attached = {**item, "content": item["content"]}

    _, system = await _run(
        _make_agent("openai"),
        "saturno",
        [item["id"]],
        attached=[attached],
    )

    assert system.count("CONTENIDO COMPLETO PRIORITARIO") == 1
    assert "## Conocimiento adjunto:" in system
    assert "## Conocimiento recuperado:" not in system
