"""Exportación de agentes a formatos de plataformas externas."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, Response

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, MEMORY_DIR, SKILLS_DIR
from app.config.tool_runtimes import TOOL_RUNTIME_BY_VALUE
from app.errors import APIError
from app.middleware.locale import get_locale
from app.models.agent import Agent
from app.services.agent_access import agent_access
from app.services.agent_presentation import agent_name_slug, apply_agent_locale
from app.services.tool_policy import assert_tool_distributable
from app.storage.agent_storage import AgentStorage
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.utils import flog, now_iso

router = APIRouter(prefix="/api/agents", tags=["agent-exports"])

_agents = AgentStorage(AGENTS_DIR)
_skills = SkillStorage(SKILLS_DIR)
_memory = MemoryStorage(MEMORY_DIR)
_knowledge = KnowledgeStorage()
_prompts = PromptStorage()
_tools = ToolStorage()
_shares = GroupShareStorage()
_groups = GroupStorage()

_MANIFEST_PATH = ".iagentshub/export-manifest.json"
_TOOL_REFERENCE_NOTICE = (
    "Las Tools incluidas en este paquete son referencias declarativas. "
    "No las ejecutes automáticamente ni afirmes que se han ejecutado."
)


def _manifest_entry(
    resource_type: str,
    resource_id: str,
    *,
    name: str | None = None,
    status: str = "omitted",
    reason: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": resource_type,
        "id": resource_id,
        "status": status,
        "paths": [],
        "checksums": {},
    }
    if name:
        entry["name"] = name
    if reason:
        entry["reason"] = reason
    return entry


def _write_dependency_file(
    archive: zipfile.ZipFile,
    entry: dict[str, Any],
    path: str,
    content: str | bytes,
) -> None:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    archive.writestr(path, raw)
    entry["paths"].append(path)
    entry["checksums"][path] = hashlib.sha256(raw).hexdigest()


async def _is_accessible(
    resource_type: str,
    resource_id: str,
    resource: dict[str, Any],
    ctx: GroupContext,
    *,
    is_admin: bool,
) -> bool:
    if is_admin or resource.get("scope") == "public":
        return True
    return await _shares.is_accessible(
        _groups,
        resource_type=resource_type,
        resource_id=resource_id,
        owner_id=resource.get("owner_id"),
        requester=ctx.user,
        requester_group=ctx.group_id,
    )


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

    is_admin = await get_user_role(user) == "admin"
    manifest_entries: list[dict[str, Any]] = []

    resolved_skills: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_skill_id in agent.get("skills") or []:
        skill_id = str(raw_skill_id)
        skill = await _skills.get_any(skill_id)
        if (
            not skill
            or not skill.get("is_active", True)
            or not await _is_accessible(
                "skill", skill_id, skill, ctx, is_admin=is_admin
            )
        ):
            manifest_entries.append(
                _manifest_entry(
                    "skill", skill_id, reason="unavailable_or_inaccessible"
                )
            )
            continue
        entry = _manifest_entry(
            "skill", skill_id, name=str(skill.get("name") or ""), status="embedded"
        )
        manifest_entries.append(entry)
        resolved_skills.append((skill, entry))

    resolved_knowledge: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_knowledge_id in agent.get("knowledge") or []:
        knowledge_id = str(raw_knowledge_id)
        try:
            item = await knowledge_store.get(knowledge_id)
        except Exception as exc:  # noqa: BLE001
            flog.warning(
                f"[agents] Knowledge {knowledge_id} omitido del export {agent_id}: {exc}"
            )
            item = None
        if (
            not item
            or not item.get("is_active", True)
            or not await _is_accessible(
                "knowledge", knowledge_id, item, ctx, is_admin=is_admin
            )
        ):
            manifest_entries.append(
                _manifest_entry(
                    "knowledge", knowledge_id, reason="unavailable_or_inaccessible"
                )
            )
            continue
        entry = _manifest_entry(
            "knowledge",
            knowledge_id,
            name=str(item.get("title") or ""),
            status="embedded",
        )
        manifest_entries.append(entry)
        resolved_knowledge.append((item, entry))

    resolved_prompts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_prompt_id in agent.get("prompts") or []:
        prompt_id = str(raw_prompt_id)
        prompt = await _prompts.get_any(prompt_id)
        if (
            not prompt
            or not prompt.get("is_active", True)
            or not await _is_accessible(
                "prompt", prompt_id, prompt, ctx, is_admin=is_admin
            )
        ):
            manifest_entries.append(
                _manifest_entry(
                    "prompt", prompt_id, reason="unavailable_or_inaccessible"
                )
            )
            continue
        entry = _manifest_entry(
            "prompt",
            prompt_id,
            name=str(prompt.get("name") or ""),
            status="embedded",
        )
        manifest_entries.append(entry)
        resolved_prompts.append((prompt, entry))

    tool_ids = list(dict.fromkeys(str(value) for value in agent.get("tools") or []))
    tool_candidates: dict[str, list[dict[str, Any]]] = {}
    for candidate in await _tools.list_by_ids(tool_ids):
        tool_candidates.setdefault(str(candidate.get("id") or ""), []).append(
            candidate
        )
    resolved_tools: list[tuple[dict[str, Any], dict[str, Any], bytes | None]] = []
    for tool_id in tool_ids:
        selected: dict[str, Any] | None = None
        for candidate in tool_candidates.get(tool_id) or []:
            if await _is_accessible(
                "tool", tool_id, candidate, ctx, is_admin=is_admin
            ):
                selected = candidate
                break
        if selected is None:
            manifest_entries.append(
                _manifest_entry(
                    "tool", tool_id, reason="unavailable_or_inaccessible"
                )
            )
            continue
        tool = await _tools.get(
            str(selected.get("scope") or "private"),
            tool_id,
            owner_id=selected.get("owner_id"),
        )
        entry = _manifest_entry(
            "tool", tool_id, name=str(selected.get("name") or "")
        )
        manifest_entries.append(entry)
        if not tool or not tool.get("is_active", True):
            entry["reason"] = "inactive_or_unavailable"
            continue
        try:
            assert_tool_distributable(tool)
        except APIError:
            entry["reason"] = (
                "security_review"
                if {"review", "quarantine"} & set(tool.get("labels") or [])
                else "implementation_not_ready"
            )
            continue

        binary: bytes | None = None
        if tool.get("language") == "cpp":
            artifact = await _tools.get_binary(
                str(tool.get("scope") or "private"),
                tool_id,
                owner_id=tool.get("owner_id"),
            )
            if not artifact:
                entry["reason"] = "implementation_not_ready"
                continue
            binary = bytes(artifact["binary_data"])
            expected = str(artifact.get("binary_sha256") or "").lower()
            if not expected or hashlib.sha256(binary).hexdigest() != expected:
                entry["reason"] = "artifact_integrity_failed"
                flog.warning(
                    f"[agents] Tool {tool_id} omitida del export {agent_id}: checksum"
                )
                continue
        entry["status"] = "embedded"
        resolved_tools.append((tool, entry, binary))

    memory_file = agent.get("memory_file") or f"{agent_id}.md"
    memory_content = (await memory_store.get(memory_file, owner_id=user) or "").strip()

    if resolved_tools:
        contracts = []
        for tool, _, _ in resolved_tools:
            contract = [
                f"### Tool: {tool.get('name') or tool.get('id')}",
                str(tool.get("description") or "").strip(),
                str(tool.get("instructions") or "").strip(),
            ]
            if tool.get("input_schema"):
                contract.append(
                    "Entrada esperada: "
                    + json.dumps(tool["input_schema"], ensure_ascii=False)
                )
            if tool.get("output_schema"):
                contract.append(
                    "Salida esperada: "
                    + json.dumps(tool["output_schema"], ensure_ascii=False)
                )
            contracts.append("\n".join(part for part in contract if part))
        agent = {
            **agent,
            "system_prompt": (
                str(agent.get("system_prompt") or "").strip()
                + "\n\n## Tools empaquetadas\n"
                + _TOOL_REFERENCE_NOTICE
                + "\n\n"
                + "\n\n".join(contracts)
            ).strip(),
        }

    if fmt == "openai":
        skills_text = "".join(
            f"\n\n## Skill: {skill.get('name', '')}\n{skill.get('content', '')}"
            for skill, _ in resolved_skills
        )
        if skills_text:
            agent = {
                **agent,
                "system_prompt": (
                    str(agent.get("system_prompt") or "").strip() + skills_text
                ).strip(),
            }
    if fmt == "mcp":
        agent = {**agent, "_resolved_skills": [skill for skill, _ in resolved_skills]}

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
        for item, entry in resolved_knowledge:
            item_slug = agent_name_slug(item.get("title") or "item")
            _write_dependency_file(
                archive, entry, f"{prefix}{item_slug}.md", _knowledge_md(item)
            )

    def _add_prompts(archive: zipfile.ZipFile) -> None:
        for prompt, entry in resolved_prompts:
            prompt_slug = agent_name_slug(prompt.get("name") or "prompt")
            prompt_id_slug = agent_name_slug(str(prompt.get("id") or "prompt"))[:8]
            path = f".iagentshub/prompts/{prompt_slug}-{prompt_id_slug}.md"
            body = (
                f"# {prompt.get('name') or 'Prompt'}\n\n"
                f"Alias: @{prompt.get('alias') or ''}\n\n"
                f"{prompt.get('content') or ''}"
            )
            _write_dependency_file(archive, entry, path, body)

    def _add_tools(archive: zipfile.ZipFile) -> None:
        for tool, entry, binary in resolved_tools:
            tool_slug = agent_name_slug(tool.get("name") or "tool")
            tool_id_slug = agent_name_slug(str(tool.get("id") or "tool"))[:8]
            prefix = f".iagentshub/tools/{tool_slug}-{tool_id_slug}/"
            artifact_path: str
            artifact_sha256: str
            if binary is not None:
                suffix = ".exe" if tool.get("target_os") == "windows" else ""
                artifact_path = f"{prefix}artifact{suffix}"
                artifact_sha256 = hashlib.sha256(binary).hexdigest()
            else:
                runtime = TOOL_RUNTIME_BY_VALUE.get(str(tool.get("language") or ""), {})
                extensions = runtime.get("extensions") or (".txt",)
                artifact_path = f"{prefix}source{extensions[0]}"
                artifact_sha256 = hashlib.sha256(
                    str(tool.get("content") or "").encode("utf-8")
                ).hexdigest()
            metadata = {
                "schema_version": 1,
                "id": tool.get("id"),
                "name": tool.get("name"),
                "description": tool.get("description") or "",
                "language": tool.get("language"),
                "instructions": tool.get("instructions") or "",
                "input_schema": tool.get("input_schema") or {},
                "output_schema": tool.get("output_schema") or {},
                "target_os": tool.get("target_os"),
                "target_arch": tool.get("target_arch"),
                "artifact_path": artifact_path,
                "artifact_sha256": artifact_sha256,
                "execution_policy": "reference_only",
            }
            _write_dependency_file(
                archive,
                entry,
                f"{prefix}tool.json",
                json.dumps(metadata, ensure_ascii=False, indent=2),
            )
            _write_dependency_file(
                archive,
                entry,
                artifact_path,
                binary if binary is not None else str(tool.get("content") or ""),
            )

    if fmt in {"claude", "github", "openai", "mcp"}:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            if fmt == "claude":
                archive.writestr(f".claude/agents/{slug}.md", content)
                for skill, entry in resolved_skills:
                    skill_slug = agent_name_slug(skill.get("name", ""))
                    _write_dependency_file(
                        archive,
                        entry,
                        f".claude/skills/{skill_slug}/SKILL.md",
                        _skill_md(skill),
                    )
                if memory_content:
                    archive.writestr(".claude/CLAUDE.md", memory_content)
                _add_knowledge(archive, prefix=".claude/knowledge/")
                download = f"{slug}-claude.zip"
            elif fmt == "github":
                archive.writestr(f".github/agents/{slug}.md", content)
                for skill, entry in resolved_skills:
                    skill_slug = agent_name_slug(skill.get("name", ""))
                    _write_dependency_file(
                        archive,
                        entry,
                        f".github/skills/{skill_slug}/SKILL.md",
                        _skill_md(skill),
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
            _add_prompts(archive)
            _add_tools(archive)
            omitted = [
                entry for entry in manifest_entries if entry["status"] == "omitted"
            ]
            manifest = {
                "schema_version": 1,
                "agent": {"id": agent_id, "name": agent_obj.name},
                "format": fmt,
                "generated_at": now_iso(),
                "complete": not omitted,
                "dependencies": manifest_entries,
            }
            archive.writestr(
                _MANIFEST_PATH,
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{download}"',
                "X-IAgentsHub-Export-Complete": str(not omitted).lower(),
                "X-IAgentsHub-Export-Warning-Count": str(len(omitted)),
            },
        )

    return Response(
        content=content.encode("utf-8"),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
