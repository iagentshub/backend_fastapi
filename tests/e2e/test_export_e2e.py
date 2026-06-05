"""E2E tests for agent export — real HTTP server, real TCP connections."""
from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from tests.e2e.conftest import create_agent, create_skill, zip_names, zip_read


# ── OpenAI ────────────────────────────────────────────────────────────────────

def test_e2e_openai_export_real_http(auth_client):
    agent = create_agent(auth_client, {"agent_type": "openai", "tool_choice": "required"})
    r = auth_client.get(f"/api/agents/{agent['id']}/export/openai")

    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('-openai.json"')

    payload = r.json()
    assert payload["tool_choice"] == "required"
    assert payload["instructions"] == "You are a test assistant."


def test_e2e_openai_export_skills_in_instructions(auth_client):
    skill = create_skill(auth_client, "Slack", "Send Slack messages.", "Use `slack send` to send.")
    agent = create_agent(auth_client, {"skills": [skill["id"]]})
    r = auth_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    instructions = r.json()["instructions"]
    assert "Slack" in instructions
    assert "slack send" in instructions


# ── Claude ────────────────────────────────────────────────────────────────────

def test_e2e_claude_export_real_http(auth_client):
    agent = create_agent(auth_client)
    r = auth_client.get(f"/api/agents/{agent['id']}/export/claude")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    assert "-claude.zip" in r.headers["content-disposition"]
    assert zipfile.is_zipfile(io.BytesIO(r.content))


def test_e2e_claude_export_zip_contents(auth_client):
    skill = create_skill(auth_client, "Weather", "Get weather data.", "Use wttr.in to get weather.")
    agent = create_agent(auth_client, {"skills": [skill["id"]]})
    r = auth_client.get(f"/api/agents/{agent['id']}/export/claude")

    names = zip_names(r.content)

    # Agent file in .claude/agents/
    agent_files = [n for n in names if n.startswith(".claude/agents/")]
    assert len(agent_files) == 1

    # Skill as command
    command_files = [n for n in names if n.startswith(".claude/commands/")]
    assert len(command_files) == 1

    # Skill as importable ZIP
    skill_zips = [n for n in names if n.startswith("skills/") and n.endswith(".zip")]
    assert len(skill_zips) == 1

    # Skill content NOT injected into agent body
    agent_body = zip_read(r.content, agent_files[0])
    assert "wttr.in" not in agent_body


# ── GitHub Copilot ────────────────────────────────────────────────────────────

def test_e2e_github_export_real_http(auth_client):
    agent = create_agent(auth_client, {"agent_type": "github", "copilot_topic": "devops"})
    r = auth_client.get(f"/api/agents/{agent['id']}/export/github")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "-copilot.zip" in r.headers["content-disposition"]


def test_e2e_github_export_skills_separate(auth_client):
    skill = create_skill(auth_client, "Docker Ops", "Manage Docker containers.", "Use `docker ps` to list.")
    agent = create_agent(auth_client, {"skills": [skill["id"]]})
    r = auth_client.get(f"/api/agents/{agent['id']}/export/github")

    names = zip_names(r.content)

    # Agent file
    agent_files = [n for n in names if n.startswith(".github/agents/")]
    assert len(agent_files) == 1

    # Skill as .github/skills/
    skill_files = [n for n in names if n.startswith(".github/skills/") and n.endswith("SKILL.md")]
    assert len(skill_files) == 1

    # Skill content NOT in agent body
    agent_body = zip_read(r.content, agent_files[0])
    assert "docker ps" not in agent_body, "Skill content must not be in agent body"

    # Skill content IS in skill file
    skill_body = zip_read(r.content, skill_files[0])
    assert "docker ps" in skill_body


def test_e2e_github_export_no_skills(auth_client):
    agent = create_agent(auth_client)
    r = auth_client.get(f"/api/agents/{agent['id']}/export/github")
    names = zip_names(r.content)
    assert not any(n.startswith(".github/skills/") for n in names)


# ── MCP ───────────────────────────────────────────────────────────────────────

def test_e2e_mcp_export_real_http(auth_client):
    agent = create_agent(auth_client)
    r = auth_client.get(f"/api/agents/{agent['id']}/export/mcp")

    assert r.status_code == 200
    assert "-mcp.py" in r.headers["content-disposition"]


def test_e2e_mcp_export_valid_python(auth_client):
    skill = create_skill(auth_client, "Send Email", "Send email via SMTP.", "Use SMTP to send.")
    agent = create_agent(auth_client, {"skills": [skill["id"]]})
    r = auth_client.get(f"/api/agents/{agent['id']}/export/mcp")

    code = r.text
    assert "from mcp.server.fastmcp import FastMCP" in code
    assert "@mcp.tool()" in code
    assert "def send_email" in code
    assert "Send email via SMTP." in code
    assert 'if __name__ == "__main__"' in code
    assert "mcp.run()" in code

    # Must be parseable Python
    import ast
    ast.parse(code)


def test_e2e_mcp_export_multiple_skills(auth_client):
    s1 = create_skill(auth_client, "Read File",  "Read a file.",  "open(path).read()")
    s2 = create_skill(auth_client, "Write File", "Write a file.", "open(path,'w').write()")
    agent = create_agent(auth_client, {"skills": [s1["id"], s2["id"]]})
    r = auth_client.get(f"/api/agents/{agent['id']}/export/mcp")
    code = r.text
    assert code.count("@mcp.tool()") == 2
    assert "def read_file" in code
    assert "def write_file" in code


# ── Error cases ───────────────────────────────────────────────────────────────

def test_e2e_export_unknown_format(auth_client):
    agent = create_agent(auth_client)
    r = auth_client.get(f"/api/agents/{agent['id']}/export/xlsx")
    assert r.status_code == 400


def test_e2e_export_not_found(auth_client):
    r = auth_client.get("/api/agents/does-not-exist/export/claude")
    assert r.status_code == 404


def test_e2e_export_requires_auth(live_server):
    base, *_ = live_server
    with httpx.Client(base_url=base) as anon:
        r = anon.get("/api/agents/any-id/export/claude")
    assert r.status_code == 401
