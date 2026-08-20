"""Exportación de agentes a formatos de plataformas externas."""

from __future__ import annotations

import io
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, Response

from app.api.routes.auth import GroupContext, require_group_session
from app.config.data import AGENTS_DIR, MEMORY_DIR, SKILLS_DIR
from app.errors import APIError
from app.middleware.locale import get_locale
from app.models.agent import Agent
from app.services.agent_access import agent_access
from app.services.agent_presentation import agent_name_slug, apply_agent_locale
from app.storage.agent_storage import AgentStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.skill_storage import SkillStorage
from app.utils import flog

router = APIRouter(prefix="/api/agents", tags=["agent-exports"])

_agents = AgentStorage(AGENTS_DIR)
_skills = SkillStorage(SKILLS_DIR)
_memory = MemoryStorage(MEMORY_DIR)
_knowledge = KnowledgeStorage()


@router.get("/{agent_id}/export/{fmt}")
async def export_agent(
    agent_id: str, fmt: str, ctx: GroupContext = Depends(require_group_session)
) -> Response:
    user = ctx.user
    agent = await _agents.get(agent_id)
    memory_store = _memory
    knowledge_store: Any = _knowledge
    if not agent:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    await agent_access.assert_can_read(agent_id, agent, ctx)
    agent = apply_agent_locale(agent, get_locale(), AGENTS_DIR)

    resolved_skills: list[dict[str, Any]] = []
    for skill_id in agent.get("skills") or []:
        for scope in ("public", "private"):
            skill = await _skills.get(scope, skill_id)
            if skill:
                resolved_skills.append(skill)
                break

    resolved_knowledge: list[dict[str, Any]] = []
    for knowledge_id in agent.get("knowledge") or []:
        try:
            item = await knowledge_store.get(knowledge_id)
            if item:
                resolved_knowledge.append(item)
        except Exception as exc:  # noqa: BLE001
            # El export no debe caerse por un item de knowledge ilegible: se
            # omite ese, se registra cuál, y el resto del agente se exporta.
            flog.warning(
                f"[agents] Knowledge {knowledge_id} omitido del export {agent_id}: {exc}"
            )

    memory_file = agent.get("memory_file") or f"{agent_id}.md"
    memory_content = (await memory_store.get(memory_file, owner_id=user) or "").strip()

    if fmt == "openai":
        skills_text = "".join(
            f"\n\n## Skill: {skill.get('name', '')}\n{skill.get('content', '')}"
            for skill in resolved_skills
        )
        if skills_text:
            agent = {
                **agent,
                "system_prompt": (
                    str(agent.get("system_prompt") or "").strip() + skills_text
                ).strip(),
            }
    if fmt == "mcp":
        agent = {**agent, "_resolved_skills": resolved_skills}

    agent_obj = Agent.from_dict(agent)
    try:
        content, media, filename = agent_obj.export(fmt)
    except NotImplementedError:
        raise APIError(
            400,
            "export_format_unsupported",
            f"Formato '{fmt}' no soportado para tipo '{agent_obj.agent_type}'",
            extra={"format": fmt, "agent_type": agent_obj.agent_type},
        ) from None

    slug = agent_name_slug(agent_obj.name)

    def _skill_md(skill: dict[str, Any]) -> str:
        name = skill.get("name", "")
        description = str(skill.get("description") or "")[:200]
        return (
            f"---\nname: {name}\ndescription: {description}\n---\n\n"
            f"{skill.get('content', '')}"
        )

    def _knowledge_md(item: dict[str, Any]) -> str:
        title = item.get("title") or "item"
        source = item.get("source") or ""
        knowledge_type = item.get("type") or "text"
        header = f"# {title}\n\n"
        if source:
            header += f"> Source: {source}  \n> Type: {knowledge_type}\n\n"
        return header + (item.get("content") or "")

    def _add_knowledge(archive: zipfile.ZipFile, prefix: str = "knowledge/") -> None:
        for item in resolved_knowledge:
            item_slug = agent_name_slug(item.get("title") or "item")
            archive.writestr(f"{prefix}{item_slug}.md", _knowledge_md(item))

    if fmt in {"claude", "github", "openai", "mcp"}:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            if fmt == "claude":
                archive.writestr(f".claude/agents/{slug}.md", content)
                for skill in resolved_skills:
                    skill_slug = agent_name_slug(skill.get("name", ""))
                    archive.writestr(
                        f".claude/skills/{skill_slug}/SKILL.md", _skill_md(skill)
                    )
                if memory_content:
                    archive.writestr(".claude/CLAUDE.md", memory_content)
                _add_knowledge(archive, prefix=".claude/knowledge/")
                download = f"{slug}-claude.zip"
            elif fmt == "github":
                archive.writestr(f".github/agents/{slug}.md", content)
                for skill in resolved_skills:
                    skill_slug = agent_name_slug(skill.get("name", ""))
                    archive.writestr(
                        f".github/skills/{skill_slug}/SKILL.md", _skill_md(skill)
                    )
                if memory_content:
                    archive.writestr(".github/COPILOT_INSTRUCTIONS.md", memory_content)
                _add_knowledge(archive, prefix=".github/knowledge/")
                download = f"{slug}-copilot.zip"
            elif fmt == "openai":
                archive.writestr("agent.json", content)
                if memory_content:
                    archive.writestr("memory.md", memory_content)
                _add_knowledge(archive)
                download = f"{slug}-openai.zip"
            else:
                archive.writestr(f"{slug}-server.py", content)
                if memory_content:
                    archive.writestr("memory.md", memory_content)
                _add_knowledge(archive)
                download = f"{slug}-mcp.zip"
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{download}"'},
        )

    return Response(
        content=content.encode("utf-8"),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
