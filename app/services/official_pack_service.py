"""Catalog projection and atomic linking for public official-source packs."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

import app.config.data as _cfg
from app.models.official_source import (
    LinkOfficialPackRequest,
    LinkOfficialPackResult,
    PublicOfficialPack,
    PublicOfficialPackComponent,
    PublicOfficialPackDetail,
)
from app.storage import db as _db
from app.storage.agent_storage import AgentStorage
from app.storage.db import AsyncConn, open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage
from app.utils.generators import generate_id

_SUPPORTED_TYPES = frozenset(
    {"skill", "knowledge", "prompt", "tool", "memory", "agent", "workflow"}
)
_DEPENDENCY_FIELDS = {
    "skills": "skill",
    "knowledge": "knowledge",
    "prompts": "prompt",
    "tools": "tool",
}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return (
        [str(item) for item in decoded if str(item)]
        if isinstance(decoded, list)
        else []
    )


def _linked_labels(labels: Iterable[str]) -> list[str]:
    result = [
        str(label)
        for label in labels
        if label and label not in {"fork", "linked", "public", "private"}
    ]
    return list(dict.fromkeys(["private", *result, "linked"]))


class OfficialPackService:
    def __init__(self) -> None:
        self.agents = AgentStorage(_cfg.AGENTS_DIR)
        self.skills = SkillStorage(_cfg.SKILLS_DIR)
        self.prompts = PromptStorage()
        self.tools = ToolStorage()
        self.knowledge = KnowledgeStorage()
        self.memory = MemoryStorage(_cfg.MEMORY_DIR)
        self.workflows = WorkflowStorage()

    async def _public_rows(
        self, source_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        where = "WHERE rs.is_public=1"
        params: tuple[Any, ...] = ()
        if source_id is not None:
            where += " AND l.source_id=?"
            params = (source_id,)
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT l.source_id,l.component_key,l.resource_type,l.resource_id,"
                "l.resource_owner_id,l.commit_sha,s.name AS source_name,"
                "s.description AS source_description,s.repository_url,"
                "s.repository_owner,s.repository_name,s.provider,s.license,"
                "s.last_commit_sha,rs.name,rs.description,rs.category,rs.tags,rs.labels,"
                "rs.stars_count,rs.updated_at FROM resource_source_links l "
                "JOIN official_sources s ON s.id=l.source_id "
                "JOIN resource_social rs ON rs.resource_type=l.resource_type "
                "AND rs.resource_id=l.resource_id AND rs.owner=l.resource_owner_id "
                f"{where}",
                params,
            )
        result: list[dict[str, Any]] = []
        for raw in rows:
            item = dict(raw)
            item["labels"] = _json_list(item.get("labels"))
            item["tags"] = _json_list(item.get("tags"))
            result.append(item)
        return result

    @staticmethod
    def _matches(
        row: dict[str, Any],
        *,
        resource_type: str,
        category: str,
        query: str,
        tag: str,
        labels: list[str],
        languages: list[str],
    ) -> bool:
        if (
            resource_type
            and resource_type != "all"
            and row["resource_type"] != resource_type
        ):
            return False
        if category and str(row.get("category") or "") != category:
            return False
        haystack = " ".join(
            str(row.get(key) or "")
            for key in (
                "name",
                "description",
                "source_name",
                "repository_owner",
                "repository_name",
            )
        ).lower()
        if query and query.lower() not in haystack:
            return False
        if tag and tag not in row["tags"]:
            return False
        if labels and not set(labels).intersection(row["labels"]):
            return False
        if languages and not {
            f"lang_{language.strip().lower()}" for language in languages
        }.intersection(row["labels"]):
            return False
        return True

    async def list_packs(
        self,
        requester_id: str,
        *,
        resource_type: str = "all",
        category: str = "",
        query: str = "",
        tag: str = "",
        labels: Optional[list[str]] = None,
        languages: Optional[list[str]] = None,
    ) -> list[PublicOfficialPack]:
        rows = await self._public_rows()
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_source[str(row["source_id"])].append(row)

        linked: Counter[str] = Counter()
        async with open_db() as conn:
            linked_rows = await conn.fetchall(
                "SELECT DISTINCT l.source_id,l.component_key FROM resource_source_links l "
                "JOIN resource_social copied ON copied.resource_type=l.resource_type "
                "AND copied.linked_to_id=l.resource_id "
                "AND copied.linked_to_user=l.resource_owner_id "
                "WHERE copied.owner=?",
                (requester_id,),
            )
        for row in linked_rows:
            linked[str(row["source_id"])] += 1

        packs: list[PublicOfficialPack] = []
        for source_id, components in by_source.items():
            matching = [
                row
                for row in components
                if self._matches(
                    row,
                    resource_type=resource_type,
                    category=category,
                    query=query,
                    tag=tag,
                    labels=labels or [],
                    languages=languages or [],
                )
            ]
            if not matching:
                continue
            first = components[0]
            counts = Counter(str(row["resource_type"]) for row in components)
            linked_count = min(linked[source_id], len(components))
            state = (
                "complete"
                if components and linked_count == len(components)
                else "partial"
                if linked_count
                else "none"
            )
            packs.append(
                PublicOfficialPack(
                    source_id=source_id,
                    name=str(first["source_name"]),
                    description=str(first.get("source_description") or ""),
                    repository_url=str(first["repository_url"]),
                    repository_owner=str(first.get("repository_owner") or ""),
                    repository_name=str(first.get("repository_name") or ""),
                    provider=str(first.get("provider") or "github"),
                    license=str(first.get("license") or ""),
                    commit_sha=str(
                        first.get("last_commit_sha") or first.get("commit_sha") or ""
                    ),
                    counts=dict(counts),
                    matching_count=len(matching),
                    total_count=len(components),
                    linked_count=linked_count,
                    link_state=state,
                    owned_by_requester=all(
                        str(row["resource_owner_id"]) == requester_id
                        for row in components
                    ),
                )
            )
        packs.sort(key=lambda item: (item.name.lower(), item.source_id))
        return packs

    async def _load_resource(self, row: dict[str, Any]) -> Any:
        kind = str(row["resource_type"])
        resource_id = str(row["resource_id"])
        owner_id = str(row["resource_owner_id"])
        if kind == "agent":
            return await self.agents.get(resource_id, owner_id=owner_id)
        if kind == "skill":
            return await self.skills.get_any(resource_id, owner_id=owner_id)
        if kind == "prompt":
            return await self.prompts.get_any(resource_id, owner_id=owner_id)
        if kind == "tool":
            return await self.tools.get_any(resource_id, owner_id=owner_id)
        if kind == "knowledge":
            return await self.knowledge.get(resource_id, owner_id)
        if kind == "workflow":
            return await self.workflows.get(resource_id, owner_id)
        if kind == "memory":
            return await self.memory.get(resource_id, owner_id)
        return None

    async def _component_rows(
        self, source_id: str, *, load_all_payloads: bool = False
    ) -> list[dict[str, Any]]:
        rows = await self._public_rows(source_id)
        if not rows:
            return []
        by_resource = {
            (str(row["resource_type"]), str(row["resource_id"])): str(
                row["component_key"]
            )
            for row in rows
        }
        for row in rows:
            row["dependencies"] = []
            kind = str(row["resource_type"])
            payload = (
                await self._load_resource(row)
                if load_all_payloads or kind in {"agent", "workflow"}
                else None
            )
            row["payload"] = payload
            if not isinstance(payload, dict):
                continue
            dependencies: list[str] = []
            if row["resource_type"] == "agent":
                for field, kind in _DEPENDENCY_FIELDS.items():
                    for resource_id in payload.get(field) or []:
                        key = by_resource.get((kind, str(resource_id)))
                        if key:
                            dependencies.append(key)
                if payload.get("use_memory") and payload.get("memory_file"):
                    memory_id = str(payload["memory_file"]).removesuffix(".md")
                    key = by_resource.get(("memory", memory_id))
                    if key:
                        dependencies.append(key)
            elif row["resource_type"] == "workflow":
                for node in payload.get("definition", {}).get("nodes", []):
                    key = by_resource.get(("agent", str(node.get("agent_id") or "")))
                    if key:
                        dependencies.append(key)
            row["dependencies"] = list(dict.fromkeys(dependencies))
        return rows

    async def detail(
        self, requester_id: str, source_id: str
    ) -> Optional[PublicOfficialPackDetail]:
        rows = await self._component_rows(source_id)
        if not rows:
            return None
        packs = await self.list_packs(requester_id)
        pack = next((item for item in packs if item.source_id == source_id), None)
        if pack is None:
            return None
        async with open_db() as conn:
            linked_rows = await conn.fetchall(
                "SELECT resource_type,linked_to_id,linked_to_user FROM resource_social "
                "WHERE owner=? AND linked_to_id IS NOT NULL",
                (requester_id,),
            )
        linked_keys = {
            (
                str(row["resource_type"]),
                str(row["linked_to_id"]),
                str(row["linked_to_user"]),
            )
            for row in linked_rows
        }
        components = [
            PublicOfficialPackComponent(
                component_key=str(row["component_key"]),
                resource_type=str(row["resource_type"]),
                resource_id=str(row["resource_id"]),
                name=str(row["name"]),
                description=str(row.get("description") or ""),
                labels=list(row["labels"]),
                dependencies=list(row["dependencies"]),
                selectable=str(row["resource_type"]) in _SUPPORTED_TYPES,
                linked=(
                    str(row["resource_type"]),
                    str(row["resource_id"]),
                    str(row["resource_owner_id"]),
                )
                in linked_keys,
            )
            for row in rows
        ]
        return PublicOfficialPackDetail(pack=pack, components=components)

    async def graph(
        self, requester_id: str, source_id: str
    ) -> Optional[dict[str, Any]]:
        detail = await self.detail(requester_id, source_id)
        if detail is None:
            return None
        root_id = f"official_source:{source_id}"
        nodes = [
            {
                "id": root_id,
                "resource_id": source_id,
                "label": detail.pack.name,
                "type": "official_source",
                "description": detail.pack.repository_url,
            }
        ]
        edges: list[dict[str, Any]] = []
        for component in detail.components:
            node_id = f"{component.resource_type}:{component.resource_id}"
            nodes.append(
                {
                    "id": node_id,
                    "resource_id": component.resource_id,
                    "label": component.name,
                    "type": component.resource_type,
                    "description": component.description,
                }
            )
            edges.append(
                {"source_id": root_id, "target_id": node_id, "relation": "origin"}
            )
        by_key = {item.component_key: item for item in detail.components}
        for component in detail.components:
            for dependency in component.dependencies:
                target = by_key.get(dependency)
                if target:
                    edges.append(
                        {
                            "source_id": f"{component.resource_type}:{component.resource_id}",
                            "target_id": f"{target.resource_type}:{target.resource_id}",
                            "relation": "uses",
                        }
                    )
        return {"root_id": root_id, "nodes": nodes, "edges": edges}

    async def link(
        self, requester_id: str, source_id: str, request: LinkOfficialPackRequest
    ) -> Optional[LinkOfficialPackResult]:
        rows = await self._component_rows(source_id, load_all_payloads=True)
        if not rows:
            return None
        if all(str(row["resource_owner_id"]) == requester_id for row in rows):
            raise PermissionError("official_pack_already_owner")
        source_commit = str(
            rows[0].get("last_commit_sha") or rows[0].get("commit_sha") or ""
        )
        if request.commit_sha and request.commit_sha != source_commit:
            raise ValueError("official_pack_stale")
        by_key = {str(row["component_key"]): row for row in rows}
        if request.mode == "all":
            explicit = {
                key
                for key, row in by_key.items()
                if row["resource_type"] in _SUPPORTED_TYPES
            }
        else:
            explicit = {str(key) for key in request.component_keys}
            if explicit - by_key.keys():
                raise ValueError("official_pack_invalid_component")
            if any(
                by_key[key]["resource_type"] not in _SUPPORTED_TYPES for key in explicit
            ):
                raise ValueError("official_pack_invalid_component")

        selected = set(explicit)
        pending = list(explicit)
        while pending:
            key = pending.pop()
            for dependency in by_key[key]["dependencies"]:
                if dependency not in selected:
                    selected.add(dependency)
                    pending.append(dependency)
        included_dependencies = sorted(selected - explicit)

        existing_map: dict[tuple[str, str, str], str] = {}
        async with open_db() as conn:
            existing_rows = await conn.fetchall(
                "SELECT resource_type,resource_id,linked_to_id,linked_to_user "
                "FROM resource_social WHERE owner=? AND linked_to_id IS NOT NULL",
                (requester_id,),
            )
        for row in existing_rows:
            existing_map[
                (
                    str(row["resource_type"]),
                    str(row["linked_to_id"]),
                    str(row["linked_to_user"]),
                )
            ] = str(row["resource_id"])

        # El enlace individual legacy de un agente clonaba sus dependencias,
        # pero solo registraba `linked_to_*` para el agente. Recuperamos esas
        # copias por posicion para que completar el pack las adopte en lugar de
        # crear skills/documentos duplicados y desconectados del agente local.
        recovered = await self._recover_legacy_agent_dependencies(
            selected, by_key, existing_map, requester_id
        )

        destination_ids: dict[str, str] = {}
        existing_result: list[dict[str, str]] = []
        for key in selected:
            row = by_key[key]
            existing_id = existing_map.get(
                (
                    str(row["resource_type"]),
                    str(row["resource_id"]),
                    str(row["resource_owner_id"]),
                )
            )
            if existing_id:
                destination_ids[key] = existing_id
                existing_result.append(
                    {
                        "component_key": key,
                        "resource_type": str(row["resource_type"]),
                        "resource_id": existing_id,
                    }
                )
            elif key in recovered:
                destination_ids[key] = recovered[key]
                existing_result.append(
                    {
                        "component_key": key,
                        "resource_type": str(row["resource_type"]),
                        "resource_id": recovered[key],
                    }
                )
            else:
                destination_ids[key] = generate_id()

        order = {
            "skill": 0,
            "knowledge": 0,
            "prompt": 0,
            "tool": 0,
            "memory": 0,
            "agent": 1,
            "workflow": 2,
        }
        created: list[dict[str, str]] = []
        async with open_db() as conn:
            async with conn.transaction():
                for key, destination_id in recovered.items():
                    if key in selected:
                        await self._record_link(
                            by_key[key], destination_id, requester_id, conn
                        )
                for key in sorted(
                    selected,
                    key=lambda item: (
                        order.get(str(by_key[item]["resource_type"]), 9),
                        item,
                    ),
                ):
                    row = by_key[key]
                    destination_id = destination_ids[key]
                    if any(item["component_key"] == key for item in existing_result):
                        continue
                    await self._copy(
                        row, destination_id, destination_ids, by_key, requester_id, conn
                    )
                    await self._record_link(row, destination_id, requester_id, conn)
                    created.append(
                        {
                            "component_key": key,
                            "resource_type": str(row["resource_type"]),
                            "resource_id": destination_id,
                        }
                    )
        return LinkOfficialPackResult(
            source_id=source_id,
            created=created,
            existing=existing_result,
            included_dependencies=included_dependencies,
        )

    async def _recover_legacy_agent_dependencies(
        self,
        selected: set[str],
        by_key: dict[str, dict[str, Any]],
        existing_map: dict[tuple[str, str, str], str],
        requester_id: str,
    ) -> dict[str, str]:
        recovered: dict[str, str] = {}
        resource_keys = {
            (str(row["resource_type"]), str(row["resource_id"])): key
            for key, row in by_key.items()
        }
        for agent_key in selected:
            agent_row = by_key[agent_key]
            if agent_row["resource_type"] != "agent":
                continue
            local_agent_id = existing_map.get(
                (
                    "agent",
                    str(agent_row["resource_id"]),
                    str(agent_row["resource_owner_id"]),
                )
            )
            source_agent = agent_row.get("payload")
            if not local_agent_id or not isinstance(source_agent, dict):
                continue
            local_agent = await self.agents.get(
                local_agent_id, scope="private", owner_id=requester_id
            )
            if not local_agent:
                continue
            for field, kind in _DEPENDENCY_FIELDS.items():
                source_ids = [str(item) for item in source_agent.get(field) or []]
                local_ids = [str(item) for item in local_agent.get(field) or []]
                for source_id, local_id in zip(source_ids, local_ids):
                    dependency_key = resource_keys.get((kind, source_id))
                    if dependency_key in selected:
                        recovered.setdefault(dependency_key, local_id)
            source_memory = str(source_agent.get("memory_file") or "").removesuffix(
                ".md"
            )
            local_memory = str(local_agent.get("memory_file") or "").removesuffix(".md")
            memory_key = resource_keys.get(("memory", source_memory))
            if memory_key in selected and local_memory:
                recovered.setdefault(memory_key, local_memory)
        return recovered

    async def _copy(
        self,
        row: dict[str, Any],
        destination_id: str,
        destination_ids: dict[str, str],
        by_key: dict[str, dict[str, Any]],
        requester_id: str,
        conn: AsyncConn,
    ) -> None:
        kind = str(row["resource_type"])
        source = row.get("payload")
        if kind == "memory":
            await self.memory.save(
                f"{destination_id}.md", str(source or ""), requester_id, conn=conn
            )
            return
        if not isinstance(source, dict):
            raise ValueError("official_pack_missing_resource")
        payload = {
            key: value
            for key, value in source.items()
            if key
            not in {
                "id",
                "owner_id",
                "scope",
                "created_at",
                "updated_at",
                "official_source_id",
                "official_component_id",
            }
        }
        payload["id"] = destination_id
        payload["labels"] = _linked_labels(source.get("labels") or [])
        if kind == "skill":
            await self.skills.save("private", payload, requester_id, conn=conn)
        elif kind == "prompt":
            payload["alias"] = await self._unique_prompt_alias(
                conn,
                requester_id,
                str(payload.get("alias") or "prompt"),
                destination_id,
            )
            await self.prompts.save("private", payload, requester_id, conn=conn)
        elif kind == "tool":
            await self.tools.save("private", payload, requester_id, conn=conn)
        elif kind == "knowledge":
            await self.knowledge.save(
                type=str(source.get("type") or "text"),
                title=str(source.get("title") or source.get("name") or row["name"]),
                source=str(source.get("source") or ""),
                content=str(source.get("content") or ""),
                owner_id=requester_id,
                labels=payload["labels"],
                item_id=destination_id,
                conn=conn,
            )
        elif kind == "agent":
            for field, dependency_type in _DEPENDENCY_FIELDS.items():
                remapped: list[str] = []
                for resource_id in source.get(field) or []:
                    match = next(
                        (
                            key
                            for key, candidate in by_key.items()
                            if candidate["resource_type"] == dependency_type
                            and str(candidate["resource_id"]) == str(resource_id)
                        ),
                        None,
                    )
                    if match and match in destination_ids:
                        remapped.append(destination_ids[match])
                payload[field] = remapped
            memory_key = next(
                (
                    dependency
                    for dependency in row["dependencies"]
                    if by_key[dependency]["resource_type"] == "memory"
                ),
                None,
            )
            payload["memory_file"] = (
                f"{destination_ids[memory_key]}.md" if memory_key else None
            )
            payload["use_memory"] = memory_key is not None
            await self.agents.save(payload, "private", requester_id, conn=conn)
        elif kind == "workflow":
            definition = json.loads(json.dumps(source.get("definition") or {}))
            for node in definition.get("nodes", []):
                old_id = str(node.get("agent_id") or "")
                match = next(
                    (
                        key
                        for key, candidate in by_key.items()
                        if candidate["resource_type"] == "agent"
                        and str(candidate["resource_id"]) == old_id
                    ),
                    None,
                )
                if match and match in destination_ids:
                    node["agent_id"] = destination_ids[match]
            payload["definition"] = definition
            payload["scope"] = "private"
            await self.workflows.save(requester_id, payload, conn=conn)
        else:
            raise ValueError("official_pack_invalid_component")

    async def _unique_prompt_alias(
        self, conn: AsyncConn, owner_id: str, alias: str, destination_id: str
    ) -> str:
        base = alias[:30] or "prompt"
        candidate = base
        suffix = 2
        while await conn.fetchone(
            "SELECT 1 FROM prompts WHERE owner_id=? AND alias=? AND id != ?",
            (owner_id, candidate, destination_id),
        ):
            tail = f"-{suffix}"
            candidate = f"{base[: 30 - len(tail)].rstrip('-_')}{tail}"
            suffix += 1
        return candidate

    async def _record_link(
        self,
        row: dict[str, Any],
        destination_id: str,
        requester_id: str,
        conn: AsyncConn,
    ) -> None:
        columns = (
            "resource_type,resource_id,owner,name,description,is_public,category,"
            "trial_missing_deps,linked_to_user,linked_to_id,tags,labels"
        )
        values = "?,?,?,?,?,0,?,'warn',?,?,?,?"
        conflict = "ON CONFLICT DO NOTHING" if _db.IS_PG else ""
        verb = "INSERT" if _db.IS_PG else "INSERT OR IGNORE"
        await conn.execute(
            f"{verb} INTO resource_social ({columns}) VALUES ({values}) {conflict}",
            (
                str(row["resource_type"]),
                destination_id,
                requester_id,
                str(row["name"]),
                str(row.get("description") or ""),
                str(row.get("category") or "Other"),
                str(row["resource_owner_id"]),
                str(row["resource_id"]),
                json.dumps(row.get("tags") or []),
                json.dumps(_linked_labels(row.get("labels") or [])),
            ),
        )
