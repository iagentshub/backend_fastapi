"""Enlazar un pack oficial: copia sus componentes al espacio del usuario.

La otra mitad de `OfficialPackService`, la que escribe.
`_recover_legacy_agent_dependencies` existe para los packs materializados antes
de que las dependencias se guardaran explícitamente.
"""


from __future__ import annotations

import json
from typing import Any, Optional

from app.models.official_source import (
    LinkOfficialPackRequest,
    LinkOfficialPackResult,
)
from app.services.official_pack_service._shared import (
    _DEPENDENCY_FIELDS,
    _SUPPORTED_TYPES,
    _linked_labels,
)
from app.sql import sql
from app.storage import db as _db
from app.storage.db import AsyncConn, open_db
from app.utils.generators import generate_id


class _PackLinkingMixin:
    async def link(
        self, requester_id: str, source_id: str, request: LinkOfficialPackRequest
    ) -> Optional[LinkOfficialPackResult]:
        rows = await self._component_rows(source_id)
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
                sql("queries/official_packs:linked_resources_of_user"),
                (requester_id,),
            )
            prompt_alias_rows = await conn.fetchall(
                sql("queries/official_packs:prompt_aliases_of_owner"), (requester_id,)
            )
        for row in existing_rows:
            existing_map[
                (
                    str(row["resource_type"]),
                    str(row["linked_to_id"]),
                    str(row["linked_to_user"]),
                )
            ] = str(row["resource_id"])
        used_prompt_aliases = {str(row["alias"]) for row in prompt_alias_rows}

        # El enlace individual legacy de un agente clonaba sus dependencias,
        # pero solo registraba `linked_to_*` para el agente. Recuperamos esas
        # copias por posicion para que completar el pack las adopte en lugar de
        # crear skills/documentos duplicados y desconectados del agente local.
        recovered = await self._recover_legacy_agent_dependencies(
            selected, by_key, existing_map, requester_id
        )

        destination_ids: dict[str, str] = {}
        existing_result: list[dict[str, str]] = []
        existing_keys: set[str] = set()
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
                existing_keys.add(key)
                existing_result.append(
                    {
                        "component_key": key,
                        "resource_type": str(row["resource_type"]),
                        "resource_id": existing_id,
                    }
                )
            elif key in recovered:
                destination_ids[key] = recovered[key]
                existing_keys.add(key)
                existing_result.append(
                    {
                        "component_key": key,
                        "resource_type": str(row["resource_type"]),
                        "resource_id": recovered[key],
                    }
                )
            else:
                destination_ids[key] = generate_id()

        payload_keys = {
            key
            for key in selected - existing_keys
            if by_key[key].get("payload") is None
        }
        selected_payloads = await self._load_resources(
            by_key[key] for key in payload_keys
        )
        for key in payload_keys:
            by_key[key]["payload"] = selected_payloads.get(
                self._payload_key(by_key[key])
            )

        for key in sorted(selected):
            row = by_key[key]
            if row["resource_type"] != "prompt" or key in existing_keys:
                continue
            payload = row.get("payload") or {}
            base = str(payload.get("alias") or "prompt")[:30] or "prompt"
            candidate = base
            suffix = 2
            while candidate in used_prompt_aliases:
                tail = f"-{suffix}"
                candidate = f"{base[: 30 - len(tail)].rstrip('-_')}{tail}"
                suffix += 1
            used_prompt_aliases.add(candidate)
            row["destination_alias"] = candidate

        order = {
            "skill": 0,
            "knowledge": 0,
            "prompt": 0,
            "tool": 0,
            "memory": 0,
            "agent": 1,
            "workflow": 2,
        }
        resource_keys = {
            (str(row["resource_type"]), str(row["resource_id"])): key
            for key, row in by_key.items()
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
                    if key in existing_keys:
                        continue
                    await self._copy(
                        row,
                        destination_id,
                        destination_ids,
                        by_key,
                        resource_keys,
                        requester_id,
                        conn,
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
        local_agent_refs: list[dict[str, Any]] = []
        local_agent_ids: dict[str, str] = {}
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
            if local_agent_id:
                local_agent_ids[agent_key] = local_agent_id
                local_agent_refs.append(
                    {
                        "resource_type": "agent",
                        "resource_id": local_agent_id,
                        "resource_owner_id": requester_id,
                    }
                )
        local_agents = await self._load_resources(local_agent_refs)
        for agent_key in selected:
            agent_row = by_key[agent_key]
            if agent_row["resource_type"] != "agent":
                continue
            local_agent_id = local_agent_ids.get(agent_key)
            source_agent = agent_row.get("payload")
            if not local_agent_id or not isinstance(source_agent, dict):
                continue
            local_agent = local_agents.get(("agent", local_agent_id, requester_id))
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
        resource_keys: dict[tuple[str, str], str],
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
            await self.skills.save(
                "private", payload, requester_id, conn=conn, assume_new=True
            )
        elif kind == "prompt":
            payload["alias"] = str(row.get("destination_alias") or "prompt")
            await self.prompts.save(
                "private", payload, requester_id, conn=conn, assume_new=True
            )
        elif kind == "tool":
            await self.tools.save(
                "private", payload, requester_id, conn=conn, assume_new=True
            )
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
                assume_new=True,
            )
        elif kind == "agent":
            for field, dependency_type in _DEPENDENCY_FIELDS.items():
                remapped: list[str] = []
                for resource_id in source.get(field) or []:
                    match = resource_keys.get((dependency_type, str(resource_id)))
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
            await self.agents.save(
                payload, "private", requester_id, conn=conn, assume_new=True
            )
        elif kind == "workflow":
            definition = json.loads(json.dumps(source.get("definition") or {}))
            for node in definition.get("nodes", []):
                old_id = str(node.get("agent_id") or "")
                match = resource_keys.get(("agent", old_id))
                if match and match in destination_ids:
                    node["agent_id"] = destination_ids[match]
            payload["definition"] = definition
            payload["scope"] = "private"
            await self.workflows.save(requester_id, payload, conn=conn, assume_new=True)
        else:
            raise ValueError("official_pack_invalid_component")

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
