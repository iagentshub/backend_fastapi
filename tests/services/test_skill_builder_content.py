from __future__ import annotations

from app.services.skill_builder import (
    SkillBuilderMessage,
    SkillDraft,
    build_from_skill_markdown,
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
