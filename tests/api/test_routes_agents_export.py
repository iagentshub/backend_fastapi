"""Tests for agent export endpoint: /api/agents/{id}/export/{fmt}."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

# ── Helpers ───────────────────────────────────────────────────────────────────

_SKILL_PAYLOAD = {
    "name": "GitHub Ops",
    "description": "GitHub operations via gh CLI.",
    "content": "Use `gh pr list` to list pull requests.",
}

_AGENT_BASE = {
    "name": "My Agent",
    "description": "A test agent",
    "system_prompt": "You are a helpful assistant.",
    "model": "gpt-4o",
    "temperature": 0.5,
}

_MANIFEST_PATH = ".iagentshub/export-manifest.json"


def _create_agent(client, extra=None):
    payload = {**_AGENT_BASE, **(extra or {})}
    r = client.post("/api/agents", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _create_skill(client):
    r = client.post("/api/skills/private", json=_SKILL_PAYLOAD)
    assert r.status_code == 200, r.text
    return r.json()


def _zip_names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.namelist()


def _zip_read(content: bytes, path: str) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.read(path).decode()


def _zip_bytes(content: bytes, path: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.read(path)


# ── OpenAI export ─────────────────────────────────────────────────────────────


def test_openai_export_is_zip(admin_client):
    agent = _create_agent(admin_client, {"agent_type": "openai"})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert zipfile.is_zipfile(io.BytesIO(r.content))


def test_openai_export_basic(admin_client):
    agent = _create_agent(
        admin_client, {"agent_type": "openai", "tool_choice": "required"}
    )
    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    payload = json.loads(_zip_read(r.content, "agent.json"))
    assert payload["name"] == "My Agent"
    assert payload["instructions"] == "You are a helpful assistant."
    assert payload["tool_choice"] == "required"


def test_openai_export_default_tool_choice(admin_client):
    agent = _create_agent(admin_client, {"agent_type": "openai"})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    payload = json.loads(_zip_read(r.content, "agent.json"))
    assert "tool_choice" in payload
    assert payload["tool_choice"] == "auto"


def test_openai_export_injects_skills(admin_client):
    skill = _create_skill(admin_client)
    agent = _create_agent(
        admin_client, {"agent_type": "openai", "skills": [skill["id"]]}
    )
    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    instructions = json.loads(_zip_read(r.content, "agent.json"))["instructions"]
    assert "GitHub Ops" in instructions
    assert "gh pr list" in instructions


# ── Claude export ─────────────────────────────────────────────────────────────


def test_claude_export_is_zip(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/claude")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert zipfile.is_zipfile(io.BytesIO(r.content))


def test_claude_export_zip_structure(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/claude")
    names = _zip_names(r.content)
    # Solo la ruta .claude/ — no debe haber directorios raíz agents/ o skills/
    hidden_agent_files = [n for n in names if n.startswith(".claude/agents/")]
    assert len(hidden_agent_files) == 1
    assert hidden_agent_files[0].endswith(".md")
    assert not any(n.startswith("agents/") for n in names), (
        "No debe haber directorio raíz agents/"
    )


def test_claude_export_agent_frontmatter(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/claude")
    names = _zip_names(r.content)
    # Solo la copia .claude/ — frontmatter correcto
    agent_md = next(n for n in names if n.startswith(".claude/agents/"))
    content = _zip_read(r.content, agent_md)
    assert "name: My Agent" in content
    assert "You are a helpful assistant." in content


def test_claude_export_skills_as_skill_files(admin_client):
    skill = _create_skill(admin_client)
    agent = _create_agent(admin_client, {"skills": [skill["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/claude")
    names = _zip_names(r.content)
    # Solo la ruta .claude/skills/ — no debe haber directorio raíz skills/
    hidden_skill_files = [n for n in names if n.startswith(".claude/skills/")]
    assert len(hidden_skill_files) == 1, "Skill must appear as a .claude/skills/ file"
    assert hidden_skill_files[0].endswith("SKILL.md"), (
        "Skill file must be named SKILL.md"
    )
    assert not any(n.startswith("skills/") for n in names), (
        "No debe haber directorio raíz skills/"
    )
    # Skill content must NOT be injected into the agent body
    agent_md = next(n for n in names if n.startswith(".claude/agents/"))
    agent_body = _zip_read(r.content, agent_md)
    assert "gh pr list" not in agent_body


# ── GitHub Copilot export ─────────────────────────────────────────────────────


def test_github_export_is_zip(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    assert r.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(r.content))


def test_github_export_agent_file(admin_client):
    agent = _create_agent(
        admin_client, {"agent_type": "github", "copilot_topic": "productivity"}
    )
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)
    # Solo la ruta .github/agents/ — no debe haber directorio raíz agents/
    hidden_agent_files = [n for n in names if n.startswith(".github/agents/")]
    assert len(hidden_agent_files) == 1
    content = _zip_read(r.content, hidden_agent_files[0])
    assert "name: My Agent" in content
    assert "topic: productivity" in content
    assert not any(n.startswith("agents/") for n in names), (
        "No debe haber directorio raíz agents/"
    )


def test_github_export_skills_as_separate_files(admin_client):
    skill = _create_skill(admin_client)
    agent = _create_agent(admin_client, {"skills": [skill["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)

    # Solo la ruta .github/skills/ — no debe haber directorio raíz skills/
    hidden_skill_files = [n for n in names if n.startswith(".github/skills/")]
    assert len(hidden_skill_files) == 1, (
        "Each skill must be a .github/skills/{slug}/SKILL.md"
    )
    assert hidden_skill_files[0].endswith("SKILL.md")
    hidden_content = _zip_read(r.content, hidden_skill_files[0])
    assert "GitHub Ops" in hidden_content
    assert "gh pr list" in hidden_content
    assert not any(n.startswith("skills/") for n in names), (
        "No debe haber directorio raíz skills/"
    )


def test_github_export_skills_not_in_agent_body(admin_client):
    """Skills must NOT be injected as text into the agent's system prompt."""
    skill = _create_skill(admin_client)
    agent = _create_agent(admin_client, {"skills": [skill["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)
    agent_md = next(n for n in names if n.startswith(".github/agents/"))
    agent_body = _zip_read(r.content, agent_md)
    assert "gh pr list" not in agent_body, "Skill content must not appear in agent body"


def test_github_export_no_skills(admin_client):
    """Agent with no skills exports only the agent file, no skills/ entries."""
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)
    assert not any(n.startswith(".github/skills/") for n in names)
    assert not any(n.startswith("skills/") for n in names)


# ── MCP export ────────────────────────────────────────────────────────────────


def _mcp_server_path(content: bytes) -> str:
    names = _zip_names(content)
    server_files = [n for n in names if n.endswith("-server.py")]
    assert len(server_files) == 1, f"Expected one -server.py, got: {names}"
    return server_files[0]


def test_mcp_export_is_zip(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert zipfile.is_zipfile(io.BytesIO(r.content))


def test_mcp_export_fastmcp_boilerplate(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = _zip_read(r.content, _mcp_server_path(r.content))
    assert "from mcp.server.fastmcp import FastMCP" in code
    assert 'FastMCP("My Agent")' in code
    assert "@mcp.tool()" in code
    assert "def " in code
    assert 'if __name__ == "__main__"' in code
    assert "mcp.run()" in code


def test_mcp_export_one_tool_per_skill(admin_client):
    skill1 = admin_client.post(
        "/api/skills/private",
        json={"name": "GitHub Ops", "description": "GitHub ops", "content": "..."},
    ).json()
    skill2 = admin_client.post(
        "/api/skills/private",
        json={"name": "Slack Messaging", "description": "Slack ops", "content": "..."},
    ).json()
    agent = _create_agent(admin_client, {"skills": [skill1["id"], skill2["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = _zip_read(r.content, _mcp_server_path(r.content))
    assert code.count("@mcp.tool()") == 2
    assert "def github_ops" in code
    assert "def slack_messaging" in code


def test_mcp_export_no_skills_fallback(admin_client):
    """Agent without skills still produces a valid tool stub."""
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = _zip_read(r.content, _mcp_server_path(r.content))
    assert "@mcp.tool()" in code
    assert "def " in code


def test_mcp_export_skill_description_as_docstring(admin_client):
    skill = _create_skill(admin_client)
    agent = _create_agent(admin_client, {"skills": [skill["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = _zip_read(r.content, _mcp_server_path(r.content))
    assert "GitHub operations via gh CLI." in code


# ── Knowledge export (all formats) ───────────────────────────────────────────


def _create_knowledge(client, title="Doc de prueba", content="Contenido de prueba."):
    r = client.post("/api/knowledge/text", json={"title": title, "content": content})
    assert r.status_code == 200, r.text
    return r.json()


def test_claude_export_includes_knowledge(admin_client):
    doc = _create_knowledge(admin_client)
    agent = _create_agent(admin_client, {"knowledge": [doc["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/claude")
    names = _zip_names(r.content)
    # Knowledge bajo .claude/knowledge/ para seguir la nomenclatura de Claude Code
    knowledge_files = [n for n in names if n.startswith(".claude/knowledge/")]
    assert len(knowledge_files) == 1
    content = _zip_read(r.content, knowledge_files[0])
    assert "Doc de prueba" in content
    assert "Contenido de prueba." in content


def test_github_export_includes_knowledge(admin_client):
    doc = _create_knowledge(admin_client)
    agent = _create_agent(admin_client, {"knowledge": [doc["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)
    # Knowledge bajo .github/knowledge/ para seguir la nomenclatura de GitHub Copilot
    knowledge_files = [n for n in names if n.startswith(".github/knowledge/")]
    assert len(knowledge_files) == 1


def test_github_export_includes_memory(admin_client):
    agent = _create_agent(admin_client)
    admin_client.post(
        f"/api/memory/{agent['id']}.md", json={"content": "Memoria de prueba"}
    )
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)
    assert ".github/COPILOT_INSTRUCTIONS.md" in names


def test_openai_export_includes_knowledge(admin_client):
    doc = _create_knowledge(admin_client)
    agent = _create_agent(
        admin_client, {"agent_type": "openai", "knowledge": [doc["id"]]}
    )
    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    names = _zip_names(r.content)
    knowledge_files = [n for n in names if n.startswith("knowledge/")]
    assert len(knowledge_files) == 1


def test_mcp_export_includes_knowledge(admin_client):
    doc = _create_knowledge(admin_client)
    agent = _create_agent(admin_client, {"knowledge": [doc["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    names = _zip_names(r.content)
    knowledge_files = [n for n in names if n.startswith("knowledge/")]
    assert len(knowledge_files) == 1


def test_export_no_knowledge_no_folder(admin_client):
    """Agents with no knowledge produce no knowledge folder in any path."""
    agent = _create_agent(admin_client)
    for fmt in ("claude", "github", "openai", "mcp"):
        r = admin_client.get(f"/api/agents/{agent['id']}/export/{fmt}")
        names = _zip_names(r.content)
        assert not any("knowledge/" in n for n in names), fmt


def test_export_omits_unreadable_knowledge_and_warns(admin_client):
    doc = _create_knowledge(admin_client)
    agent = _create_agent(admin_client, {"knowledge": [doc["id"]]})

    with (
        patch(
            "app.api.routes.agent_exports._knowledge.get",
            new=AsyncMock(side_effect=RuntimeError("lectura fallida")),
        ),
        patch("app.api.routes.agent_exports.flog.warning") as warning,
    ):
        response = admin_client.get(f"/api/agents/{agent['id']}/export/claude")

    assert response.status_code == 200
    assert not any("knowledge/" in name for name in _zip_names(response.content))
    assert "omitido del export" in warning.call_args.args[0]


def test_export_packages_prompts_and_safe_tools_with_manifest(admin_client):
    prompt = admin_client.post(
        "/api/prompts/private",
        json={
            "name": "Review Prompt",
            "description": "Prompt reusable",
            "content": "Revisa el cambio con atención.",
            "alias": "review-change",
        },
    ).json()
    source_tool = admin_client.post(
        "/api/tools/private",
        json={
            "name": "Source Tool",
            "description": "Tool de fuente",
            "language": "python",
            "content": "print('safe source')",
            "instructions": "Recibe un nombre.",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "string"},
        },
    ).json()
    binary_tool = admin_client.post(
        "/api/tools/private",
        json={
            "name": "Binary Tool",
            "description": "Tool binaria",
            "language": "cpp",
            "target_os": "linux",
            "target_arch": "x64",
        },
    ).json()
    binary = b"\x7fELF\x02\x01" + (b"\x00" * 12) + b"\x3e\x00"
    uploaded = admin_client.post(
        f"/api/tools/private/{binary_tool['id']}/binary",
        files={"file": ("binary-tool", binary, "application/octet-stream")},
    )
    assert uploaded.status_code == 200, uploaded.text
    reviewed = admin_client.post(
        "/api/tools/private",
        json={
            **binary_tool,
            "labels": ["private"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    blocked_tool = admin_client.post(
        "/api/tools/private",
        json={
            "name": "Blocked Tool",
            "description": "Pendiente de revisión",
            "language": "python",
            "content": "print('blocked')",
            "labels": ["private", "review"],
        },
    ).json()
    agent = _create_agent(
        admin_client,
        {
            "agent_type": "openai",
            "prompts": [prompt["id"]],
            "tools": [source_tool["id"], binary_tool["id"], blocked_tool["id"]],
        },
    )

    response = admin_client.get(f"/api/agents/{agent['id']}/export/openai")

    assert response.status_code == 200, response.text
    assert response.headers["x-iagentshub-export-complete"] == "false"
    assert response.headers["x-iagentshub-export-warning-count"] == "1"
    manifest = json.loads(_zip_read(response.content, _MANIFEST_PATH))
    assert manifest["schema_version"] == 1
    assert manifest["complete"] is False
    dependencies = manifest["dependencies"]
    prompt_entry = next(item for item in dependencies if item["type"] == "prompt")
    assert "Revisa el cambio" in _zip_read(response.content, prompt_entry["paths"][0])

    tool_entries = {
        item["id"]: item for item in dependencies if item["type"] == "tool"
    }
    assert tool_entries[source_tool["id"]]["status"] == "embedded"
    assert tool_entries[binary_tool["id"]]["status"] == "embedded"
    blocked_entry = tool_entries[blocked_tool["id"]]
    assert blocked_entry["status"] == "omitted"
    assert blocked_entry["reason"] == "security_review"
    assert blocked_entry["paths"] == []

    binary_entry = tool_entries[binary_tool["id"]]
    artifact_path = next(
        path for path in binary_entry["paths"] if not path.endswith("tool.json")
    )
    assert _zip_bytes(response.content, artifact_path) == binary
    for entry in dependencies:
        for path, expected in entry["checksums"].items():
            actual = hashlib.sha256(_zip_bytes(response.content, path)).hexdigest()
            assert actual == expected

    exported_agent = json.loads(_zip_read(response.content, "agent.json"))
    assert "No las ejecutes automáticamente" in exported_agent["instructions"]
    assert "Recibe un nombre" in exported_agent["instructions"]


# ── Unknown format ────────────────────────────────────────────────────────────


def test_export_unknown_format(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/unknown")
    assert r.status_code == 400


def test_export_nonexistent_agent(admin_client):
    r = admin_client.get("/api/agents/ghost-agent-id/export/claude")
    assert r.status_code == 404
