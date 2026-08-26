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
from app.pagination.models import OffsetParams
from app.services.official_source_importer.references import reference_candidates
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


async def _all_scoped(storage: Any, ctx: GroupContext) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await storage.list_visible_page(
            user=ctx.user,
            active_group_id=ctx.group_id,
            scope="all",
            page=OffsetParams(limit=100, offset=offset),
        )
        items.extend(page.items)
        if not page.has_more:
            return items
        offset += len(page.items)


async def _all_knowledge(ctx: GroupContext) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await _knowledge.list_visible_page(
            user=ctx.user,
            owner_id=ctx.group_id,
            type=None,
            page=OffsetParams(limit=100, offset=offset),
        )
        items.extend(page.items)
        if not page.has_more:
            return items
        offset += len(page.items)


async def _all_packs(ctx: GroupContext) -> list[dict[str, Any]]:
    return await _packs.list_visible(ctx.group_id, ctx.user)


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
    async def load(cls, ctx: GroupContext) -> "AgentImportCatalog":
        skills, knowledge, packs, prompts, tools = await asyncio.gather(
            _all_scoped(_skills, ctx),
            _all_knowledge(ctx),
            _all_packs(ctx),
            _all_scoped(_prompts, ctx),
            _all_scoped(_tools, ctx),
        )
        return cls(
            {
                "skill": _candidates(skills, "name"),
                "knowledge": _candidates(knowledge, "title"),
                "knowledge_pack": _candidates(packs, "name"),
                "prompt": _candidates(prompts, "name"),
                "tool": _candidates(tools, "name"),
            }
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
