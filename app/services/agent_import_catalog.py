"""Accessible resource catalog and exact import-reference resolution."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Iterable

from app.api.routes.auth import GroupContext
from app.config.data import SKILLS_DIR
from app.models.agent_import import (
    AgentImportCandidate,
    AgentImportPreview,
    AgentImportReference,
    AgentImportResourceKind,
)
from app.pagination.models import OffsetPage, OffsetParams
from app.services.official_source_importer.references import reference_candidates
from app.services.resource_visibility import VisibilityFilter
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage

_skills = SkillStorage(SKILLS_DIR)
_knowledge = KnowledgeStorage()
_packs = KnowledgePackStorage()
_prompts = PromptStorage()
_tools = ToolStorage()


def _alias_filter(
    *, alias: str, name_columns: tuple[str, ...], values: Iterable[str]
) -> VisibilityFilter:
    aliases = sorted(
        {
            candidate.lower()
            for value in values
            for candidate in reference_candidates(value)
            if candidate
        }
    )
    columns = ("id", *name_columns)
    clauses = [
        f"LOWER({alias}.{column}) IN ({','.join('?' for _ in aliases)})"
        for column in columns
    ]
    return VisibilityFilter(
        sql="(" + " OR ".join(clauses) + ")",
        params=tuple(aliases * len(columns)),
    )


def _text_filter(
    *, alias: str, name_columns: tuple[str, ...], query: str
) -> VisibilityFilter:
    escaped = (
        query.strip()
        .lower()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    columns = ("id", *name_columns)
    return VisibilityFilter(
        sql="("
        + " OR ".join(
            f"LOWER({alias}.{column}) LIKE ? ESCAPE '\\'" for column in columns
        )
        + ")",
        params=tuple(f"%{escaped}%" for _ in columns),
    )


def _chunks(values: Iterable[str], size: int = 40) -> Iterable[list[str]]:
    # One source expands to several canonical aliases and Knowledge compares
    # three columns. Keep every statement below conservative SQLite/Postgres
    # bind-parameter limits while still resolving references in batches.
    items = list(dict.fromkeys(value for value in values if value.strip()))
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


async def _matching_scoped(
    storage: Any, ctx: GroupContext, values: Iterable[str]
) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(values):
        offset = 0
        while True:
            page = await storage.list_visible_page(
                user=ctx.user,
                active_group_id=ctx.group_id,
                scope="all",
                page=OffsetParams(limit=200, offset=offset),
                catalog_filter=_alias_filter(
                    alias="resource_row", name_columns=("name",), values=chunk
                ),
            )
            matches.update(
                (str(item.get("id") or ""), item)
                for item in page.items
                if item.get("id")
            )
            if not page.has_more:
                break
            offset += len(page.items)
    return list(matches.values())


async def _matching_knowledge(
    ctx: GroupContext, values: Iterable[str]
) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(values):
        offset = 0
        while True:
            page = await _knowledge.list_visible_page(
                user=ctx.user,
                owner_id=ctx.group_id,
                type=None,
                page=OffsetParams(limit=200, offset=offset),
                catalog_filter=_alias_filter(
                    alias="k", name_columns=("title", "source"), values=chunk
                ),
            )
            matches.update(
                (str(item.get("id") or ""), item)
                for item in page.items
                if item.get("id")
            )
            if not page.has_more:
                break
            offset += len(page.items)
    return list(matches.values())


async def _matching_packs(
    ctx: GroupContext, values: Iterable[str]
) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(values):
        offset = 0
        while True:
            page = await _packs.list_visible_page(
                ctx.group_id,
                ctx.user,
                page=OffsetParams(limit=200, offset=offset),
                catalog_filter=_alias_filter(
                    alias="p", name_columns=("name",), values=chunk
                ),
            )
            matches.update(
                (str(item.get("id") or ""), item)
                for item in page.items
                if item.get("id")
            )
            if not page.has_more:
                break
            offset += len(page.items)
    return list(matches.values())


async def _knowledge_in_packs(
    ctx: GroupContext, pack_ids: Iterable[str]
) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(pack_ids):
        placeholders = ",".join("?" for _ in chunk)
        offset = 0
        while True:
            page = await _knowledge.list_visible_page(
                user=ctx.user,
                owner_id=ctx.group_id,
                type=None,
                page=OffsetParams(limit=200, offset=offset),
                catalog_filter=VisibilityFilter(
                    sql=f"k.pack_id IN ({placeholders})", params=tuple(chunk)
                ),
            )
            matches.update(
                (str(item.get("id") or ""), item)
                for item in page.items
                if item.get("id")
            )
            if not page.has_more:
                break
            offset += len(page.items)
    return list(matches.values())


async def _rows_for_queries(
    ctx: GroupContext,
    normalized: dict[AgentImportResourceKind, list[str]],
) -> dict[AgentImportResourceKind, list[dict[str, Any]]]:
    tasks: dict[AgentImportResourceKind, Any] = {}
    if normalized.get("skill"):
        tasks["skill"] = _matching_scoped(_skills, ctx, normalized["skill"])
    if normalized.get("knowledge"):
        tasks["knowledge"] = _matching_knowledge(ctx, normalized["knowledge"])
    if normalized.get("prompt"):
        tasks["prompt"] = _matching_scoped(_prompts, ctx, normalized["prompt"])
    if normalized.get("tool"):
        tasks["tool"] = _matching_scoped(_tools, ctx, normalized["tool"])
    if normalized.get("knowledge_pack"):
        tasks["knowledge_pack"] = _matching_packs(
            ctx, normalized["knowledge_pack"]
        )
    kinds = list(tasks)
    rows = await asyncio.gather(*(tasks[kind] for kind in kinds))
    found = dict(zip(kinds, rows, strict=True))
    if pack_queries := normalized.get("knowledge_pack"):
        pack_catalog = AgentImportCatalog(
            {
                "knowledge_pack": _candidates(
                    found.get("knowledge_pack", []), "name"
                )
            }
        )
        pack_ids = {
            item.id
            for query in pack_queries
            for item in pack_catalog.matches("knowledge_pack", query)
        }
        found["knowledge_pack"] = [
            row
            for row in found.get("knowledge_pack", [])
            if str(row.get("id") or "") in pack_ids
        ]
        pack_knowledge = await _knowledge_in_packs(ctx, pack_ids)
        found["knowledge"] = list(
            {
                str(row.get("id") or ""): row
                for row in [*found.get("knowledge", []), *pack_knowledge]
                if row.get("id")
            }.values()
        )
    return found


class AgentImportCatalog:
    """Typed, already-authorized candidates indexed once for a whole plan."""

    def __init__(
        self, values: dict[AgentImportResourceKind, list[AgentImportCandidate]]
    ):
        self.values = values
        self._ids = {
            kind: frozenset(candidate.id for candidate in candidates)
            for kind, candidates in values.items()
        }
        self._aliases: dict[
            AgentImportResourceKind, dict[str, list[AgentImportCandidate]]
        ] = {}
        for kind, candidates in values.items():
            aliases: dict[str, list[AgentImportCandidate]] = defaultdict(list)
            for candidate in candidates:
                keys = {
                    candidate.id.lower(),
                    *reference_candidates(candidate.name),
                }
                for key in keys:
                    aliases[key].append(candidate)
            self._aliases[kind] = aliases

    @classmethod
    async def load_for_queries(
        cls,
        ctx: GroupContext,
        queries: dict[AgentImportResourceKind, Iterable[str]],
    ) -> "AgentImportCatalog":
        """Load only authorized rows that can satisfy the supplied aliases."""

        normalized = {
            kind: list(dict.fromkeys(value for value in values if value.strip()))
            for kind, values in queries.items()
        }
        found = await _rows_for_queries(ctx, normalized)
        candidates = {
            "skill": _candidates(found.get("skill", []), "name"),
            "knowledge": _candidates(found.get("knowledge", []), "title"),
            "knowledge_pack": _candidates(
                found.get("knowledge_pack", []), "name"
            ),
            "prompt": _candidates(found.get("prompt", []), "name"),
            "tool": _candidates(found.get("tool", []), "name"),
        }
        return cls(candidates)

    @classmethod
    async def resolve_rows(
        cls,
        ctx: GroupContext,
        queries: dict[AgentImportResourceKind, Iterable[str]],
    ) -> dict[AgentImportResourceKind, list[dict[str, Any]]]:
        normalized = {
            kind: list(dict.fromkeys(value for value in values if value.strip()))
            for kind, values in queries.items()
        }
        return await _rows_for_queries(ctx, normalized)

    @classmethod
    async def search_page(
        cls,
        ctx: GroupContext,
        kind: AgentImportResourceKind,
        *,
        query: str,
        page: OffsetParams,
    ) -> OffsetPage[AgentImportCandidate]:
        """Return one authorized compact page without touching other types."""

        catalog_filter = None
        if query.strip():
            alias = "k" if kind == "knowledge" else "resource_row"
            names = ("title", "source") if kind == "knowledge" else ("name",)
            catalog_filter = _text_filter(
                alias=alias, name_columns=names, query=query
            )
        if kind in {"skill", "prompt", "tool"}:
            storage = {"skill": _skills, "prompt": _prompts, "tool": _tools}[kind]
            result = await storage.list_visible_page(
                user=ctx.user,
                active_group_id=ctx.group_id,
                scope="all",
                page=page,
                catalog_filter=catalog_filter,
            )
            return OffsetPage(
                items=_candidates(result.items, "name"),
                total=result.total,
                params=page,
            )
        if kind == "knowledge":
            result = await _knowledge.list_visible_page(
                user=ctx.user,
                owner_id=ctx.group_id,
                type=None,
                page=page,
                catalog_filter=catalog_filter,
            )
            return OffsetPage(
                items=_candidates(result.items, "title"),
                total=result.total,
                params=page,
            )
        result = await _packs.list_visible_page(
            ctx.group_id,
            ctx.user,
            page=page,
            catalog_filter=(
                _text_filter(alias="p", name_columns=("name",), query=query)
                if query.strip()
                else None
            ),
        )
        return OffsetPage(
            items=_candidates(result.items, "name"),
            total=result.total,
            params=page,
        )

    def candidates(self, kind: AgentImportResourceKind) -> list[AgentImportCandidate]:
        return self.values.get(kind, [])

    def contains(self, kind: AgentImportResourceKind, resource_id: str) -> bool:
        return resource_id in self._ids.get(kind, ())

    def matches(
        self, kind: AgentImportResourceKind, value: str
    ) -> list[AgentImportCandidate]:
        matches: dict[str, AgentImportCandidate] = {}
        aliases = self._aliases.get(kind, {})
        for candidate in reference_candidates(value):
            for match in aliases.get(candidate, []):
                matches[match.id] = match
        return sorted(matches.values(), key=lambda item: (item.name.lower(), item.id))

    def resolve(self, reference: AgentImportReference) -> AgentImportReference:
        ordered = self.matches(reference.kind, reference.source)
        if len(ordered) == 1:
            return reference.model_copy(
                update={
                    "status": "matched",
                    "selected_id": ordered[0].id,
                    "candidates": ordered,
                }
            )
        if ordered:
            return reference.model_copy(
                update={"status": "ambiguous", "candidates": ordered}
            )
        return reference.model_copy(
            update={"status": "missing", "selected_id": None, "candidates": []}
        )

    def resolve_preview(self, preview: AgentImportPreview) -> AgentImportPreview:
        return preview.model_copy(
            update={
                "references": [
                    self.resolve(reference) for reference in preview.references
                ]
            }
        )


def _candidates(
    rows: Iterable[dict[str, Any]], name_field: str
) -> list[AgentImportCandidate]:
    unique: dict[str, AgentImportCandidate] = {}
    for row in rows:
        resource_id = str(row.get("id") or "").strip()
        name = str(row.get(name_field) or row.get("name") or resource_id).strip()
        if resource_id:
            unique[resource_id] = AgentImportCandidate(id=resource_id, name=name)
    return sorted(unique.values(), key=lambda item: (item.name.lower(), item.id))
