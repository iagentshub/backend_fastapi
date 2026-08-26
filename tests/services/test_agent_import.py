import json

import pytest

from app.errors import APIError
from app.models.agent_import import AgentImportCandidate
from app.services.agent_import import parse_agent_import
from app.services.agent_import_catalog import AgentImportCatalog


def test_markdown_frontmatter_builds_private_claude_draft() -> None:
    preview = parse_agent_import(
        ".claude/agents/reviewer.md",
        b"""---
name: Reviewer
description: "Reviews pull requests"
model: claude-sonnet
permissionMode: plan
skills:
  - Security
temperature: 0.3
---

Review the change carefully.
""",
    )

    assert preview.filename == "reviewer.md"
    assert preview.source_format == "claude_markdown"
    assert preview.draft.agent_type == "claude"
    assert preview.draft.name == "Reviewer"
    assert preview.draft.scope == "private"
    assert preview.draft.labels == ["private"]
    assert preview.draft.system_prompt == "Review the change carefully."
    assert preview.draft.temperature == 0.3
    assert [(item.kind, item.source) for item in preview.references] == [
        ("skill", "Security")
    ]
    assert {issue.code for issue in preview.issues} == {
        "fields_ignored",
        "resource_references_found",
    }


def test_json_openai_aliases_and_untrusted_fields_are_sanitized() -> None:
    content = json.dumps(
        {
            "agent": {
                "id": "foreign-id",
                "owner_id": "victim",
                "scope": "public",
                "official": True,
                "title": "JSON Agent",
                "instructions": "Do the work",
                "model": "gpt-test",
                "temperature": 4,
                "tools": [{"name": "Browser"}],
            }
        }
    ).encode()

    preview = parse_agent_import("agent.json", content)

    assert preview.source_format == "openai_json"
    assert preview.draft.name == "JSON Agent"
    assert preview.draft.agent_type == "openai"
    assert preview.draft.temperature == 0.7
    assert preview.draft.scope == "private"
    assert [(item.kind, item.source) for item in preview.references] == [
        ("tool", "Browser")
    ]
    assert set(preview.ignored_fields) >= {"id", "official", "owner_id", "scope"}
    assert {issue.code for issue in preview.issues} >= {
        "fields_ignored",
        "invalid_temperature",
        "resource_references_found",
    }


def test_resource_references_are_not_silently_truncated() -> None:
    names = [f"Skill {index}" for index in range(40)]
    preview = parse_agent_import(
        "many.md",
        (
            "---\nname: Many\nskills:\n"
            + "".join(f"  - {name}\n" for name in names)
            + "---\nPrompt"
        ).encode(),
    )

    assert [reference.source for reference in preview.references] == names


def test_catalog_contains_uses_prebuilt_id_index() -> None:
    values = {
        "skill": [AgentImportCandidate(id="skill-1", name="One")],
    }
    catalog = AgentImportCatalog(values)  # type: ignore[arg-type]
    values["skill"].clear()

    assert catalog.contains("skill", "skill-1") is True
    assert catalog.contains("skill", "missing") is False


@pytest.mark.parametrize(
    ("filename", "content", "reason"),
    [
        ("agent.txt", b"hello", "unsupported_extension"),
        ("agent.md", b"", "empty"),
        ("agent.md", b"\xff", "invalid_encoding"),
        ("agent.md", b"---\nname: Agent\n", "invalid_frontmatter"),
        ("agent.json", b"[]", "invalid_json_shape"),
    ],
)
def test_invalid_files_return_structured_reason(
    filename: str,
    content: bytes,
    reason: str,
) -> None:
    with pytest.raises(APIError) as caught:
        parse_agent_import(filename, content)

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "invalid_field"
    assert caught.value.detail["field"] == "file"
    assert caught.value.detail["reason"] == reason
