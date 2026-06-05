"""Tests for agent export endpoint: /api/agents/{id}/export/{fmt}."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

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


# ── OpenAI export ─────────────────────────────────────────────────────────────

def test_openai_export_basic(admin_client):
    agent = _create_agent(admin_client, {"agent_type": "openai", "tool_choice": "required"})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    payload = r.json()
    assert payload["name"] == "My Agent"
    assert payload["instructions"] == "You are a helpful assistant."
    assert payload["tool_choice"] == "required"


def test_openai_export_default_tool_choice(admin_client):
    agent = _create_agent(admin_client, {"agent_type": "openai"})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    payload = r.json()
    assert "tool_choice" in payload
    assert payload["tool_choice"] == "auto"


def test_openai_export_injects_skills(admin_client):
    skill = _create_skill(admin_client)
    agent = _create_agent(admin_client, {"agent_type": "openai", "skills": [skill["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    instructions = r.json()["instructions"]
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
    agent_files = [n for n in names if n.startswith(".claude/agents/")]
    assert len(agent_files) == 1
    assert agent_files[0].endswith(".md")


def test_claude_export_agent_frontmatter(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/claude")
    names = _zip_names(r.content)
    agent_md = next(n for n in names if n.startswith(".claude/agents/"))
    content = _zip_read(r.content, agent_md)
    assert "name: My Agent" in content
    assert "You are a helpful assistant." in content


def test_claude_export_skills_as_commands(admin_client):
    skill = _create_skill(admin_client)
    agent = _create_agent(admin_client, {"skills": [skill["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/claude")
    names = _zip_names(r.content)
    command_files = [n for n in names if n.startswith(".claude/commands/")]
    skill_zips = [n for n in names if n.startswith("skills/") and n.endswith(".zip")]
    assert len(command_files) == 1, "Skill must appear as a .claude/commands/ file"
    assert len(skill_zips) == 1, "Skill must also have a native .zip for import"
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
    agent = _create_agent(admin_client, {"agent_type": "github", "copilot_topic": "productivity"})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)
    agent_files = [n for n in names if n.startswith(".github/agents/")]
    assert len(agent_files) == 1
    content = _zip_read(r.content, agent_files[0])
    assert "name: My Agent" in content
    assert "topic: productivity" in content


def test_github_export_skills_as_separate_files(admin_client):
    skill = _create_skill(admin_client)
    agent = _create_agent(admin_client, {"skills": [skill["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)

    skill_files = [n for n in names if n.startswith(".github/skills/")]
    assert len(skill_files) == 1, "Each skill must be a .github/skills/{slug}/SKILL.md"
    assert skill_files[0].endswith("SKILL.md")

    skill_content = _zip_read(r.content, skill_files[0])
    assert "GitHub Ops" in skill_content
    assert "gh pr list" in skill_content


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
    """Agent with no skills exports only the agent file, no .github/skills/ entries."""
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/github")
    names = _zip_names(r.content)
    assert not any(n.startswith(".github/skills/") for n in names)


# ── MCP export ────────────────────────────────────────────────────────────────

def test_mcp_export_is_python_file(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    assert r.status_code == 200
    disp = r.headers.get("content-disposition", "")
    assert disp.endswith('-mcp.py"')


def test_mcp_export_fastmcp_boilerplate(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = r.text
    assert "from mcp.server.fastmcp import FastMCP" in code
    assert 'FastMCP("My Agent")' in code
    assert "@mcp.tool()" in code
    assert "def " in code
    assert 'if __name__ == "__main__"' in code
    assert "mcp.run()" in code


def test_mcp_export_one_tool_per_skill(admin_client):
    skill1 = admin_client.post("/api/skills/private", json={
        "name": "GitHub Ops", "description": "GitHub ops", "content": "..."
    }).json()
    skill2 = admin_client.post("/api/skills/private", json={
        "name": "Slack Messaging", "description": "Slack ops", "content": "..."
    }).json()
    agent = _create_agent(admin_client, {"skills": [skill1["id"], skill2["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = r.text
    assert code.count("@mcp.tool()") == 2
    assert "def github_ops" in code
    assert "def slack_messaging" in code


def test_mcp_export_no_skills_fallback(admin_client):
    """Agent without skills still produces a valid tool stub."""
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = r.text
    assert "@mcp.tool()" in code
    assert "def " in code


def test_mcp_export_skill_description_as_docstring(admin_client):
    skill = _create_skill(admin_client)
    agent = _create_agent(admin_client, {"skills": [skill["id"]]})
    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = r.text
    assert "GitHub operations via gh CLI." in code


# ── Unknown format ────────────────────────────────────────────────────────────

def test_export_unknown_format(admin_client):
    agent = _create_agent(admin_client)
    r = admin_client.get(f"/api/agents/{agent['id']}/export/unknown")
    assert r.status_code == 400


def test_export_nonexistent_agent(admin_client):
    r = admin_client.get("/api/agents/ghost-agent-id/export/claude")
    assert r.status_code == 404
