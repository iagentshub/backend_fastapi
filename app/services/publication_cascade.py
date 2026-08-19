"""Publicar un recurso publica lo que necesita para funcionar.

Un agente público cuyas skills siguen privadas aparece en Explorar y no se
puede usar, así que publicar arrastra en cascada las dependencias propias. Cada
tipo tiene su cascada porque lo que arrastra es distinto: un agente lleva
skills, conocimiento, prompts, tools y memoria; una orquestación lleva agentes.

Salió de `routes/social.py`, donde eran ~500 líneas de lógica de negocio dentro
de la capa de rutas.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from app.services.resource_stores import (
    _agents_store,
    _knowledge_packs_store,
    _knowledge_store,
    _prompts_store,
    _skills_store,
    _tools_store,
)
from app.services.social_catalog import (
    _PUBLIC_VAL,
    _upsert_social,
)
from app.sql import sql
from app.storage.db import open_db
from app.utils.generators import generate_date


def _agent_public_dependency_keys(agent: Dict[str, Any]) -> set[str]:
    keys = {
        f"{kind}:{resource_id}"
        for kind, field_name in (
            ("skill", "skills"),
            ("knowledge", "knowledge"),
            ("knowledge_pack", "knowledge_packs"),
            ("prompt", "prompts"),
            ("tool", "tools"),
        )
        for resource_id in (agent.get(field_name) or [])
        if resource_id
    }
    memory_file = str(agent.get("memory_file") or "").strip()
    if agent.get("use_memory") and memory_file:
        keys.add(f"memory:{memory_file}")
    return keys

async def _publish_skill_cascade(
    skill_id: str, username: str, owner_ids: set[str]
) -> None:
    skill_storage = _skills_store
    skill = await skill_storage.get_any(skill_id)
    if not skill or skill.get("owner_id") not in owner_ids:
        return
    labels = list(skill.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
        await skill_storage.save(
            "private", {**skill, "labels": labels}, owner_id=skill["owner_id"]
        )
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/social:linked_to_id"),
            ("skill", skill_id, username),
        )
        if row and row["linked_to_id"]:
            return
        await _upsert_social(
            conn,
            "skill",
            skill_id,
            username,
            skill.get("name", skill_id),
            skill.get("description", ""),
            "Other",
            "warn",
            "[]",
            1,
            json.dumps(labels),
        )
        await conn.commit()

async def _publish_tool_cascade(
    tool_id: str, username: str, owner_ids: set[str]
) -> None:
    tool_storage = _tools_store
    tool = await tool_storage.get_any(tool_id)
    if not tool or tool.get("owner_id") not in owner_ids:
        return
    labels = list(tool.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
        await tool_storage.save(
            "private", {**tool, "labels": labels}, owner_id=tool["owner_id"]
        )
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/social:linked_to_id"),
            ("tool", tool_id, username),
        )
        if row and row["linked_to_id"]:
            return
        await _upsert_social(
            conn,
            "tool",
            tool_id,
            username,
            tool.get("name", tool_id),
            tool.get("description", ""),
            "Other",
            "warn",
            "[]",
            1,
            json.dumps(labels),
        )
        await conn.commit()

async def _publish_prompt_cascade(
    prompt_id: str, username: str, owner_ids: set[str]
) -> None:
    prompt_storage = _prompts_store
    prompt = await prompt_storage.get_any(prompt_id)
    if not prompt or prompt.get("owner_id") not in owner_ids:
        return
    labels = list(prompt.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
        await prompt_storage.save(
            "private", {**prompt, "labels": labels}, owner_id=prompt["owner_id"]
        )
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/social:linked_to_id"),
            ("prompt", prompt_id, username),
        )
        if row and row["linked_to_id"]:
            return
        await _upsert_social(
            conn,
            "prompt",
            prompt_id,
            username,
            prompt.get("name", prompt_id),
            prompt.get("description", ""),
            "Other",
            "warn",
            "[]",
            1,
            json.dumps(labels),
        )
        await conn.commit()

async def _publish_knowledge_cascade(
    knowledge_id: str, username: str, owner_ids: set[str]
) -> None:
    item = await _knowledge_store.get(knowledge_id)
    if not item or item.get("owner_id") not in owner_ids:
        return
    labels = list(item.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
        item = await _knowledge_store.save(
            type=item.get("type", "text"),
            title=item.get("title", knowledge_id),
            source=item.get("source", ""),
            content=item.get("content", ""),
            owner_id=item["owner_id"],
            labels=labels,
            item_id=knowledge_id,
        )
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/social:linked_to_id"),
            ("knowledge", knowledge_id, username),
        )
        if row and row["linked_to_id"]:
            return
        await _upsert_social(
            conn,
            "knowledge",
            knowledge_id,
            username,
            item.get("title", knowledge_id),
            item.get("source", ""),
            "Other",
            "warn",
            "[]",
            1,
            json.dumps(labels),
        )
        await conn.commit()

async def _publish_knowledge_pack_cascade(
    pack_id: str, username: str, owner_ids: set[str]
) -> None:
    pack = await _knowledge_packs_store.get(pack_id)
    if not pack or pack.get("owner_id") not in owner_ids:
        return
    labels = list(pack.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
    async with open_db() as conn:
        await conn.execute(
            sql("queries/social:pack_make_public"),
            (json.dumps(labels), generate_date(), pack_id),
        )
        await _upsert_social(
            conn,
            "knowledge_pack",
            pack_id,
            username,
            pack.get("name", pack_id),
            pack.get("description", ""),
            "Other",
            "warn",
            "[]",
            _PUBLIC_VAL,
            json.dumps(labels),
        )
        await conn.commit()
    for item in pack.get("items") or []:
        await _publish_knowledge_cascade(str(item.get("id") or ""), username, owner_ids)

async def sync_knowledge_visibility_from_labels(
    *,
    resource_type: str,
    resource_id: str,
    username: str,
    owner_ids: set[str],
    is_public: bool,
) -> None:
    """Sincroniza Explorar con la etiqueta de visibilidad de Knowledge."""
    if resource_type == "knowledge_pack":
        if is_public:
            await _publish_knowledge_pack_cascade(resource_id, username, owner_ids)
            return
        async with open_db() as conn:
            await conn.execute(
                sql("queries/social:pack_make_private"),
                (generate_date(), resource_id),
            )
            await conn.execute(
                sql("queries/social:delete_social_pack_cascade"),
                (resource_id, resource_id),
            )
            await conn.commit()
        return

    if resource_type != "knowledge":
        raise ValueError(f"Tipo de conocimiento no soportado: {resource_type}")
    if is_public:
        await _publish_knowledge_cascade(resource_id, username, owner_ids)
        return
    async with open_db() as conn:
        await conn.execute(
            sql("queries/social:delete_social_knowledge"),
            (resource_id,),
        )
        await conn.commit()

async def _cascade_publish_agent(
    agent: Dict[str, Any],
    username: str,
    group_id: str = "",
    selected: set[str] | None = None,
) -> None:
    """Publica únicamente las dependencias elegidas por el propietario.

    ``None`` mantiene compatibilidad con agentes públicos creados antes de que
    existiera la selección explícita. Un conjunto vacío publica solo el agente.
    Las conexiones no forman parte de este catálogo ni se aceptan como claves.
    """
    owner_ids = {username, group_id} - {""}
    for skill_id in agent.get("skills") or []:
        if selected is None or f"skill:{skill_id}" in selected:
            await _publish_skill_cascade(skill_id, username, owner_ids)
    for knowledge_id in agent.get("knowledge") or []:
        if selected is None or f"knowledge:{knowledge_id}" in selected:
            await _publish_knowledge_cascade(knowledge_id, username, owner_ids)
    for pack_id in agent.get("knowledge_packs") or []:
        if selected is None or f"knowledge_pack:{pack_id}" in selected:
            await _publish_knowledge_pack_cascade(pack_id, username, owner_ids)
    for prompt_id in agent.get("prompts") or []:
        if selected is None or f"prompt:{prompt_id}" in selected:
            await _publish_prompt_cascade(prompt_id, username, owner_ids)
    for tool_id in agent.get("tools") or []:
        if selected is None or f"tool:{tool_id}" in selected:
            await _publish_tool_cascade(tool_id, username, owner_ids)

async def _cascade_publish_workflow(
    workflow: Dict[str, Any], username: str, group_id: str = ""
) -> None:
    """Al publicar una orquestación, publica en cascada los agentes propios que usa
    (y, para cada uno, sus skills/conocimiento — ver _cascade_publish_agent)."""
    owner_ids = {str(workflow.get("owner_id") or ""), username, group_id} - {""}
    agents_storage = _agents_store
    seen: set[str] = set()
    for node in workflow.get("definition", {}).get("nodes", []):
        agent_id = str(node.get("agent_id") or "")
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        agent = await agents_storage.get(agent_id)
        if not agent or agent.get("owner_id") not in owner_ids:
            continue
        async with open_db() as conn:
            linked_row = await conn.fetchone(
                sql("queries/social:linked_to_id"),
                ("agent", agent_id, username),
            )
        if linked_row and linked_row["linked_to_id"]:
            continue
        labels = list(agent.get("labels") or ["private"])
        if "public" not in labels:
            labels.append("public")
            agent = await agents_storage.save(
                {**agent, "labels": labels},
                agent.get("scope", "private"),
                owner_id=agent["owner_id"],
            )
        async with open_db() as conn:
            await _upsert_social(
                conn,
                "agent",
                agent_id,
                username,
                agent.get("name", agent_id),
                agent.get("description", ""),
                "Other",
                "warn",
                json.dumps(agent.get("tags") or []),
                _PUBLIC_VAL,
                json.dumps(labels),
            )
            await conn.commit()
        await _cascade_publish_agent(agent, username, group_id)
