"""Listar packs oficiales y componer el detalle de uno.

Es la mitad de `OfficialPackService` que solo lee. Va como mixin y no como
clase aparte para que los métodos se sigan llamando entre sí por `self`, que es
como estaban escritos: ningún cuerpo cambió al moverlos.
"""


from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

from app.models.official_source import (
    PublicOfficialPack,
    PublicOfficialPackComponent,
    PublicOfficialPackDetail,
)
from app.services.official_pack_service._shared import (
    _DEPENDENCY_FIELDS,
    _SUPPORTED_TYPES,
    _json_list,
)
from app.sql import sql
from app.storage.db import open_db
from app.storage.knowledge import _coerce_active


class _PackListingMixin:
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
        relation: str = "all",
    ) -> list[PublicOfficialPack]:
        rows = await self._public_rows()
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_source[str(row["source_id"])].append(row)

        linked: Counter[str] = Counter()
        async with open_db() as conn:
            linked_rows = await conn.fetchall(
                sql("queries/official_packs:source_links_copied_by_user"),
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
            # Un pack a medias no está "ya tenido": todavía quedan componentes
            # por descubrir, así que sigue en el catálogo. Solo desaparece de
            # ahí cuando ya no queda nada nuevo dentro.
            if relation == "new" and state == "complete":
                continue
            if relation == "linked" and state == "none":
                continue
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

    async def _load_resources(
        self, rows: Iterable[dict[str, Any]]
    ) -> dict[tuple[str, str, str], Any]:
        """Load official resources in one query per resource type.

        Source links include the owner, so decoding the exact ``(type, id,
        owner)`` tuple also avoids the ambiguous cross-owner lookup performed
        by several legacy ``get_any`` methods.
        """

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            kind = str(row["resource_type"])
            if kind in _SUPPORTED_TYPES:
                grouped[kind].append(row)
        if not grouped:
            return {}

        table_by_type = {
            "agent": "agents",
            "skill": "skills",
            "prompt": "prompts",
            "tool": "tools",
            "knowledge": "knowledge_items",
            "memory": "memory_files",
            "workflow": "agent_workflows",
        }
        loaded: dict[tuple[str, str, str], Any] = {}
        async with open_db() as conn:
            for kind, kind_rows in grouped.items():
                ids = sorted({str(row["resource_id"]) for row in kind_rows})
                owners = sorted({str(row["resource_owner_id"]) for row in kind_rows})
                owner_marks = ",".join("?" for _ in owners)
                for start in range(0, len(ids), 400):
                    id_chunk = ids[start : start + 400]
                    id_marks = ",".join("?" for _ in id_chunk)
                    db_rows = await conn.fetchall(
                        f"SELECT * FROM {table_by_type[kind]} "
                        f"WHERE id IN ({id_marks}) AND owner_id IN ({owner_marks})",
                        tuple([*id_chunk, *owners]),
                    )
                    for db_row in db_rows:
                        key = (kind, str(db_row["id"]), str(db_row["owner_id"]))
                        if kind == "agent":
                            loaded[key] = self.agents._row_to_dict(db_row)
                        elif kind == "skill":
                            loaded[key] = self.skills._row_to_dict(db_row)
                        elif kind == "prompt":
                            loaded[key] = self.prompts._row_to_dict(db_row)
                        elif kind == "tool":
                            loaded[key] = self.tools._row_to_dict(db_row)
                        elif kind == "knowledge":
                            loaded[key] = _coerce_active(dict(db_row))
                        elif kind == "memory":
                            loaded[key] = str(db_row["content"])
                        elif kind == "workflow":
                            loaded[key] = self.workflows._decode(db_row)
        return loaded

    @staticmethod
    def _payload_key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(row["resource_type"]),
            str(row["resource_id"]),
            str(row["resource_owner_id"]),
        )

    async def _component_rows(self, source_id: str) -> list[dict[str, Any]]:
        rows = await self._public_rows(source_id)
        if not rows:
            return []
        by_resource = {
            (str(row["resource_type"]), str(row["resource_id"])): str(
                row["component_key"]
            )
            for row in rows
        }
        relationship_payloads = await self._load_resources(
            row for row in rows if row["resource_type"] in {"agent", "workflow"}
        )
        for row in rows:
            row["dependencies"] = []
            payload = relationship_payloads.get(self._payload_key(row))
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
        async with open_db() as conn:
            linked_rows = await conn.fetchall(
                sql("queries/official_packs:components_copied_from_source"),
                (source_id, requester_id),
            )
        linked_keys = {
            (
                str(row["resource_type"]),
                str(row["linked_to_id"]),
                str(row["linked_to_user"]),
            )
            for row in linked_rows
        }
        first = rows[0]
        counts = Counter(str(row["resource_type"]) for row in rows)
        linked_count = min(len(linked_keys), len(rows))
        pack = PublicOfficialPack(
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
            matching_count=len(rows),
            total_count=len(rows),
            linked_count=linked_count,
            link_state=(
                "complete"
                if rows and linked_count == len(rows)
                else "partial"
                if linked_count
                else "none"
            ),
            owned_by_requester=all(
                str(row["resource_owner_id"]) == requester_id for row in rows
            ),
        )
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
