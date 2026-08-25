"""Herencia de recursos al ENLAZAR un agente o una orquestación ajena.

Vivía en `routes/social.py` pero su único consumidor es `routes/resource_linking.py`:
esto es lo que pasa al enlazar, no al publicar. Clona (o referencia) las
skills, el conocimiento, los prompts, las tools y la memoria de los que depende
el recurso enlazado.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.errors import APIError
from app.services.resource_stores import (
    _agents_store,
    _knowledge_store,
    _memory_store,
    _prompts_store,
    _skills_store,
    _tools_store,
)
from app.services.tool_policy import assert_tool_distributable
from app.storage.db import open_db
from app.utils.generators import generate_id

# Los storages que usa _inherit_resource_ids son estos mismos: eran una segunda
# tanda de instancias con la misma configuración.
_inherit_skills_store = _skills_store

_inherit_knowledge_store = _knowledge_store

_inherit_memory_store = _memory_store

_inherit_prompts_store = _prompts_store

_inherit_tools_store = _tools_store


async def _inherit_resource_ids(
    ids: List[str], resource_type: str, target_owner_id: str
) -> List[str]:
    """Al enlazar un agente, sus skills/conocimiento/prompts privados se clonan
    junto con él (heredados) para que sigan siendo accesibles desde el nuevo
    dueño. Los públicos, o los que ya pertenecen al destino, se referencian tal
    cual (sin clonar)."""
    new_ids: List[str] = []
    for rid in ids:
        if resource_type == "skill":
            item = await _inherit_skills_store.get_any(rid)
        elif resource_type == "prompt":
            item = await _inherit_prompts_store.get_any(rid)
        elif resource_type == "tool":
            item = await _inherit_tools_store.get_any(rid)
        else:
            item = await _inherit_knowledge_store.get(rid)
        if not item:
            continue
        if resource_type == "tool":
            # Defensa en profundidad para cualquier heredador actual o futuro.
            assert_tool_distributable(item)
        if item.get("owner_id") == target_owner_id or item.get("scope") == "public":
            new_ids.append(rid)
            continue
        if resource_type == "skill":
            # id propio (no derivado del nombre) para no colisionar con una skill
            # homónima de otro owner — GET /api/skills/{scope}/{id} no filtra por
            # owner_id, así que dos ids iguales de dueños distintos son ambiguos.
            clone = {
                k: v for k, v in item.items() if k not in ("id", "scope", "owner_id")
            }
            clone["id"] = generate_id()
            clone["labels"] = [
                lbl
                for lbl in (clone.get("labels") or ["private"])
                if lbl not in ("linked", "public")
            ] or ["private"]
            saved = await _inherit_skills_store.save(
                "private", clone, owner_id=target_owner_id
            )
        elif resource_type == "prompt":
            clone = {
                k: v for k, v in item.items() if k not in ("id", "scope", "owner_id")
            }
            clone["id"] = generate_id()
            clone["labels"] = [
                lbl
                for lbl in (clone.get("labels") or ["private"])
                if lbl not in ("linked", "public")
            ] or ["private"]
            # El alias de origen puede colisionar con uno ya existente del
            # destino — nunca debe romper el clonado, se sufija si hace falta.
            clone["alias"] = await _inherit_prompts_store.unique_alias(
                target_owner_id, str(clone.get("alias") or "")
            )
            saved = await _inherit_prompts_store.save(
                "private", clone, owner_id=target_owner_id
            )
        elif resource_type == "tool":
            # id propio (no derivado del nombre) — mismo motivo que skill: no
            # colisionar con una tool homónima de otro owner.
            clone = {
                k: v for k, v in item.items() if k not in ("id", "scope", "owner_id")
            }
            clone["id"] = generate_id()
            clone["labels"] = [
                lbl
                for lbl in (clone.get("labels") or ["private"])
                if lbl not in ("linked", "public")
            ] or ["private"]
            async with open_db() as conn:
                async with conn.transaction(immediate=True):
                    saved = await _inherit_tools_store.save(
                        "private",
                        clone,
                        owner_id=target_owner_id,
                        conn=conn,
                        assume_new=True,
                    )
                    if item.get("language") == "cpp":
                        copied = await _inherit_tools_store.copy_binary(
                            str(item.get("scope") or "private"),
                            rid,
                            str(saved["id"]),
                            target_owner_id,
                            conn=conn,
                        )
                        if not copied:
                            raise APIError(
                                409,
                                "artifact_unavailable",
                                "El artefacto binario de la Tool no está disponible",
                                extra={"resource": "tool", "resource_id": rid},
                            )
        else:
            saved = await _inherit_knowledge_store.save(
                type=item.get("type", "url"),
                title=item.get("title", rid),
                source=item.get("source", ""),
                content=item.get("content", ""),
                owner_id=target_owner_id,
            )
        new_ids.append(saved["id"])
    return new_ids


async def _inherit_agent_memory(
    source_agent: Dict[str, Any],
    source_owner: str,
    new_agent_id: str,
    target_owner_id: str,
) -> None:
    """Copia el contenido de memoria del agente original al nuevo, si usa memoria."""
    if not source_agent.get("use_memory"):
        return
    mem_name = source_agent.get("memory_file") or f"{source_agent.get('id')}.md"
    content = await _inherit_memory_store.get(mem_name, source_owner)
    if content:
        await _inherit_memory_store.save(
            f"{new_agent_id}.md", content, owner_id=target_owner_id
        )


async def _inherit_workflow_agents(
    nodes: List[Dict[str, Any]], target_owner_id: str
) -> List[Dict[str, Any]]:
    """Al enlazar una orquestación, clona (o referencia) los agentes que usa,
    igual que _inherit_resource_ids hace con skills/knowledge de un agente."""
    agents_storage = _agents_store
    id_map: Dict[str, str] = {}
    new_nodes: List[Dict[str, Any]] = []
    for node in nodes:
        old_agent_id = str(node.get("agent_id") or "")
        if old_agent_id in id_map:
            new_agent_id = id_map[old_agent_id]
        else:
            agent = await agents_storage.get(old_agent_id)
            if not agent:
                new_agent_id = old_agent_id
            elif (
                agent.get("owner_id") == target_owner_id
                or agent.get("scope") == "public"
            ):
                new_agent_id = old_agent_id
            else:
                clone_payload = {
                    k: v
                    for k, v in agent.items()
                    if k not in ("id", "scope", "owner_id", "created_at", "updated_at")
                }
                clone_payload["id"] = generate_id()
                clone_payload["labels"] = [
                    lbl
                    for lbl in (clone_payload.get("labels") or ["private"])
                    if lbl not in ("linked", "fork", "public")
                ] or ["private"]
                clone_payload["connection_id"] = None
                clone_payload["op_connections"] = []
                raw_selection = agent.get("public_dependencies")
                selected = (
                    {str(value) for value in raw_selection if value}
                    if raw_selection is not None
                    else None
                )
                for kind, field_name in (
                    ("skill", "skills"),
                    ("knowledge", "knowledge"),
                    ("prompt", "prompts"),
                    ("tool", "tools"),
                ):
                    clone_payload[field_name] = [
                        resource_id
                        for resource_id in (clone_payload.get(field_name) or [])
                        if selected is None or f"{kind}:{resource_id}" in selected
                    ]
                clone_payload["skills"] = await _inherit_resource_ids(
                    clone_payload.get("skills") or [], "skill", target_owner_id
                )
                clone_payload["knowledge"] = await _inherit_resource_ids(
                    clone_payload.get("knowledge") or [], "knowledge", target_owner_id
                )
                clone_payload["prompts"] = await _inherit_resource_ids(
                    clone_payload.get("prompts") or [], "prompt", target_owner_id
                )
                clone_payload["tools"] = await _inherit_resource_ids(
                    clone_payload.get("tools") or [], "tool", target_owner_id
                )
                memory_file = str(agent.get("memory_file") or "").strip()
                copy_memory = bool(
                    agent.get("use_memory")
                    and memory_file
                    and (selected is None or f"memory:{memory_file}" in selected)
                )
                clone_payload["memory_file"] = (
                    f"{clone_payload['id']}.md" if copy_memory else None
                )
                if not copy_memory:
                    clone_payload["use_memory"] = False
                saved = await agents_storage.save(
                    clone_payload, "private", owner_id=target_owner_id
                )
                if copy_memory:
                    await _inherit_agent_memory(
                        agent,
                        str(agent.get("owner_id") or ""),
                        saved["id"],
                        target_owner_id,
                    )
                new_agent_id = saved["id"]
            id_map[old_agent_id] = new_agent_id
        new_nodes.append({**node, "agent_id": new_agent_id})
    return new_nodes
