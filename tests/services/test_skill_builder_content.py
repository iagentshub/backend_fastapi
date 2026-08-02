from __future__ import annotations

import json

import pytest

from app.services.skill_builder import (
    SkillBuilderMessage,
    SkillDraft,
    build_fallback_ready,
    build_from_skill_markdown,
    parse_builder_reply,
)


def test_builder_accepts_unlimited_message_and_skill_content():
    message = SkillBuilderMessage(role="user", content="x" * 50_000)
    draft = SkillDraft(name="Skill grande", content="x" * 50_000)

    assert len(message.content) == 50_000
    assert len(draft.content) == 50_000


def test_complete_skill_markdown_is_imported_without_provider():
    markdown = """---
name: high-end-visual-design
description: Enseña a diseñar interfaces visuales de alta calidad.
---

# Directiva

Conserva íntegramente estas instrucciones.
"""
    result = build_from_skill_markdown(
        [SkillBuilderMessage(role="user", content=markdown)]
    )

    assert result is not None
    assert result.status == "ready"
    assert result.draft is not None
    assert result.draft.name == "high-end-visual-design"
    assert result.draft.description.startswith("Enseña")
    assert "Conserva íntegramente" in result.draft.content


def test_structured_skill_requires_complete_operational_fields():
    reply = json.dumps(
        {
            "assistant_message": "Skill lista",
            "status": "ready",
            "draft": {
                "name": "Revisión de código",
                "description": "Revisa cambios antes de integrarlos",
                "category": "dev",
                "purpose": "Detectar defectos y riesgos en cambios de código.",
                "when_to_use": ["Antes de integrar una rama"],
                "inputs": ["Diff y contexto del cambio"],
                "steps": [
                    "Comprender el objetivo del cambio",
                    "Revisar corrección, seguridad y pruebas",
                    "Priorizar hallazgos con una solución concreta",
                ],
                "checks": ["Cada hallazgo incluye evidencia verificable"],
                "limits": ["No inventar errores sin evidencia"],
                "output": "Lista priorizada de hallazgos o confirmación sin incidencias.",
            },
        }
    )

    result = parse_builder_reply(reply)

    assert result.draft is not None
    assert "## Procedimiento" in result.draft.content
    assert "## Comprobaciones" in result.draft.content
    assert "## Límites" in result.draft.content


def test_structured_skill_rejects_missing_steps_and_checks():
    reply = json.dumps(
        {
            "assistant_message": "Skill lista",
            "status": "ready",
            "draft": {
                "name": "Skill genérica",
                "purpose": "Ayudar con una tarea",
                "when_to_use": ["Cuando se solicite"],
                "inputs": ["Petición"],
                "steps": ["Hacer la tarea"],
                "checks": [],
                "limits": ["No inventar"],
                "output": "Respuesta",
            },
        }
    )

    with pytest.raises(ValueError, match="instrucciones operativas completas"):
        parse_builder_reply(reply)


def test_skill_fallback_is_structured_and_actionable():
    result = build_fallback_ready(
        [SkillBuilderMessage(role="user", content="Crear informes de ventas claros")]
    )

    assert result.draft is not None
    assert len(result.draft.content) >= 180
    assert "## Procedimiento" in result.draft.content
    assert "## Comprobaciones" in result.draft.content
    assert "## Límites" in result.draft.content
