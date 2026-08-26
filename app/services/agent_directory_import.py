"""Plan and atomically materialize graph-aware local agent directories."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Iterable, Sequence

from app.api.routes.auth import GroupContext
from app.config import data as _cfg
from app.config.directory_import import (
    DIRECTORY_IMPORT_MAX_DEPTH,
    DIRECTORY_IMPORT_MAX_PATH_LENGTH,
)
from app.errors import APIError
from app.models.agent_import import (
    AgentDirectoryApplyOptions,
    AgentDirectoryComponent,
    AgentDirectoryImportPlan,
    AgentDirectoryImportResult,
    AgentImportIssue,
    AgentImportReference,
)
from app.models.official_source import PackageComponent
from app.services.agent_import import parse_agent_import
from app.services.agent_import_catalog import AgentImportCatalog
from app.services.agent_import_types import (
    COMPONENT_TYPE_TO_RESOURCE_KIND,
    RESOURCE_KIND_TO_AGENT_FIELD,
    SUPPORTED_DIRECTORY_COMPONENT_TYPES,
)
from app.services.directory_file_rules import (
    AGENT_IGNORED_DIRECTORY_NAMES,
    AGENT_SECRET_FILE_NAMES,
    InvalidDirectoryPath,
    directory_skip_reason,
    normalize_relative_path,
)
from app.services.official_source_importer.detection import detect_components
from app.services.official_source_importer.references import (
    component_aliases,
    reference_candidates,
)
from app.services.official_source_sync import order_by_dependencies, strip_frontmatter
from app.storage.agent_storage import AgentStorage
from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage


def _invalid_directory(message: str, *, reason: str) -> APIError:
    return APIError(
        422,
        "invalid_field",
        message,
        extra={"field": "files", "reason": reason},
    )


def normalize_directory_path(raw: str) -> str:
    """Normalize an untrusted browser/native relative path without escaping root."""

    try:
        return normalize_relative_path(
            raw,
            max_depth=DIRECTORY_IMPORT_MAX_DEPTH,
            max_length=DIRECTORY_IMPORT_MAX_PATH_LENGTH,
        )
    except InvalidDirectoryPath as exc:
        message = (
            "Una ruta de la carpeta supera los límites permitidos"
            if exc.reason == "path_too_long"
            else "La carpeta contiene una ruta no válida"
        )
        raise _invalid_directory(
            message, reason=exc.reason
        ) from None


def agent_directory_skip_reason(path: str) -> str | None:
    return directory_skip_reason(
        path,
        ignored_directory_names=AGENT_IGNORED_DIRECTORY_NAMES,
        secret_file_names=AGENT_SECRET_FILE_NAMES,
    )


def decode_directory_files(
    uploads: Iterable[tuple[str, bytes]],
) -> tuple[dict[str, str], list[str]]:
    """Decode safe UTF-8 files and preserve ignored paths for the review UI."""

    files: dict[str, str] = {}
    ignored: list[str] = []
    for raw_path, content in uploads:
        path = normalize_directory_path(raw_path)
        if path in files:
            raise _invalid_directory(
                "La carpeta contiene rutas duplicadas", reason="duplicate_path"
            )
        if agent_directory_skip_reason(path):
            ignored.append(path)
            continue
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            ignored.append(path)
            continue
        if "\x00" in text:
            ignored.append(path)
            continue
        files[path] = text
    if not files:
        raise _invalid_directory(
            "La carpeta no contiene archivos compatibles", reason="empty"
        )
    return files, sorted(ignored)


def _component_alias_index(
    components: Iterable[PackageComponent],
) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for component in components:
        for alias in component_aliases(component):
            index[alias].add(component.component_id)
    return dict(index)


def _local_target(
    reference: AgentImportReference,
    agent: PackageComponent,
    dependency_ids: set[str],
    by_id: dict[str, PackageComponent],
    alias_index: dict[str, set[str]],
) -> PackageComponent | None:
    matches: set[str] = set()
    for candidate in reference_candidates(reference.source, agent.source_path):
        matches.update(alias_index.get(candidate, ()))
    matches.intersection_update(dependency_ids)
    return by_id[next(iter(matches))] if len(matches) == 1 else None


class AgentDirectoryImportService:
    """Shared planner/materializer used by both directory endpoints."""

    def __init__(self) -> None:
        self.agents = AgentStorage(_cfg.AGENTS_DIR)
        self.skills = SkillStorage(_cfg.SKILLS_DIR)
        self.knowledge = KnowledgeStorage()
        self.prompts = PromptStorage()
        self.tools = ToolStorage()
        self._planned_catalog: AgentImportCatalog | None = None

    async def plan(
        self,
        uploads: Iterable[tuple[str, bytes]],
        ctx: GroupContext,
        *,
        catalog: AgentImportCatalog | None = None,
        additional_catalog_queries: dict[str, list[str]] | None = None,
    ) -> tuple[AgentDirectoryImportPlan, list[PackageComponent]]:
        files, ignored_paths = decode_directory_files(uploads)
        detected = detect_components(
            "local-directory", files, calculate_content_hashes=False
        )
        supported = [
            item
            for item in detected
            if item.component_type in SUPPORTED_DIRECTORY_COMPONENT_TYPES
        ]
        by_id = {item.component_id: item for item in supported}
        agent_previews = {
            item.component_id: parse_agent_import(
                item.source_path, item.content.encode("utf-8")
            )
            for item in supported
            if item.component_type == "agent"
            and PurePosixPath(item.source_path).suffix.lower() in {".md", ".json"}
        }
        if catalog is None:
            catalog_queries: dict[str, list[str]] = defaultdict(list)
            for item in supported:
                resource_kind = COMPONENT_TYPE_TO_RESOURCE_KIND.get(
                    item.component_type
                )
                if resource_kind:
                    catalog_queries[resource_kind].append(item.name)
            for preview in agent_previews.values():
                for reference in preview.references:
                    catalog_queries[reference.kind].append(reference.source)
            for kind, values in (additional_catalog_queries or {}).items():
                catalog_queries[kind].extend(values)
            catalog = await AgentImportCatalog.load_for_queries(  # type: ignore[arg-type]
                ctx, catalog_queries
            )
        self._planned_catalog = catalog
        alias_index = _component_alias_index(
            item
            for item in supported
            if item.component_type in COMPONENT_TYPE_TO_RESOURCE_KIND
        )
        components: list[AgentDirectoryComponent] = []
        issues: list[AgentImportIssue] = []

        for component in supported:
            kind = (
                "prompt"
                if component.component_type == "command"
                else component.component_type
            )
            if kind == "agent":
                if PurePosixPath(component.source_path).suffix.lower() not in {
                    ".md",
                    ".json",
                }:
                    ignored_paths.append(component.source_path)
                    continue
                preview = agent_previews[component.component_id]
                dependency_order = list(
                    dict.fromkeys(
                        dependency
                        for dependency in component.dependencies
                        if dependency in by_id
                        and by_id[dependency].component_type
                        in COMPONENT_TYPE_TO_RESOURCE_KIND
                    )
                )
                dependency_ids = set(dependency_order)
                references: list[AgentImportReference] = []
                local_ids: set[str] = set()
                for reference in preview.references:
                    local = _local_target(
                        reference,
                        component,
                        dependency_ids,
                        by_id,
                        alias_index,
                    )
                    if local:
                        local_ids.add(local.component_id)
                        references.append(
                            reference.model_copy(
                                update={
                                    "status": "local",
                                    "local_component_id": local.component_id,
                                }
                            )
                        )
                    else:
                        references.append(catalog.resolve(reference))
                for dependency_id in dependency_order:
                    if dependency_id in local_ids:
                        continue
                    dependency = by_id[dependency_id]
                    references.append(
                        AgentImportReference(
                            key=f"local:{dependency_id}",
                            kind=COMPONENT_TYPE_TO_RESOURCE_KIND[
                                dependency.component_type
                            ],
                            source=dependency.name,
                            status="local",
                            local_component_id=dependency_id,
                        )
                    )
                components.append(
                    AgentDirectoryComponent(
                        component_id=component.component_id,
                        kind="agent",
                        name=preview.draft.name,
                        description=preview.draft.description,
                        source_path=component.source_path,
                        content_hash=component.content_hash,
                        agent=preview.draft,
                        references=references,
                    )
                )
                continue

            resource_kind = COMPONENT_TYPE_TO_RESOURCE_KIND[component.component_type]
            existing = catalog.matches(resource_kind, component.name)
            default_action = (
                "reuse" if len(existing) == 1 else "review" if existing else "create"
            )
            components.append(
                AgentDirectoryComponent(
                    component_id=component.component_id,
                    kind=kind,  # type: ignore[arg-type]
                    name=component.name,
                    description=component.description,
                    source_path=component.source_path,
                    content_hash=component.content_hash,
                    default_action="skip"
                    if component.security_blocked
                    else default_action,
                    existing_candidates=existing,
                    selected_existing_id=existing[0].id if len(existing) == 1 else None,
                    security_blocked=component.security_blocked,
                )
            )

        unsupported = [
            item
            for item in detected
            if item.component_type not in SUPPORTED_DIRECTORY_COMPONENT_TYPES
        ]
        if unsupported:
            issues.append(
                AgentImportIssue(
                    code="unsupported_components",
                    values=sorted({item.component_type for item in unsupported}),
                )
            )
        if not any(item.kind == "agent" for item in components):
            issues.append(AgentImportIssue(code="no_agents_found"))
        plan = AgentDirectoryImportPlan(
            components=components,
            issues=issues,
            ignored_paths=sorted(set(ignored_paths)),
        )
        return plan, supported

    async def apply(
        self,
        uploads: Sequence[tuple[str, bytes]] | None,
        options: AgentDirectoryApplyOptions,
        ctx: GroupContext,
        *,
        prepared: tuple[AgentDirectoryImportPlan, list[PackageComponent]] | None = None,
    ) -> AgentDirectoryImportResult:
        additional_queries: dict[str, list[str]] = defaultdict(list)
        for choice in options.component_choices:
            if choice.resource_id:
                additional_queries["knowledge"].append(choice.resource_id)
                additional_queries["knowledge_pack"].append(choice.resource_id)
                additional_queries["prompt"].append(choice.resource_id)
                additional_queries["skill"].append(choice.resource_id)
                additional_queries["tool"].append(choice.resource_id)
        for choice in options.reference_choices:
            if choice.resource_id:
                additional_queries["knowledge"].append(choice.resource_id)
                additional_queries["knowledge_pack"].append(choice.resource_id)
                additional_queries["prompt"].append(choice.resource_id)
                additional_queries["skill"].append(choice.resource_id)
                additional_queries["tool"].append(choice.resource_id)
        if prepared is None:
            if uploads is None:
                raise RuntimeError("directory import uploads were not supplied")
            plan, detected = await self.plan(
                uploads,
                ctx,
                additional_catalog_queries=additional_queries,
            )
            catalog = self._planned_catalog
            if catalog is None:  # Defensive: plan() always sets it above.
                raise RuntimeError("directory import catalog was not planned")
        else:
            plan, detected = prepared
            for item in plan.components:
                if item.selected_existing_id:
                    additional_queries[item.kind].append(item.selected_existing_id)
                for reference in item.references:
                    if reference.selected_id:
                        additional_queries[reference.kind].append(
                            reference.selected_id
                        )
            catalog = await AgentImportCatalog.load_for_queries(  # type: ignore[arg-type]
                ctx, additional_queries
            )
        planned = {item.component_id: item for item in plan.components}
        selected_ids = set(options.selected_agent_ids)
        if not selected_ids:
            raise _invalid_directory(
                "Selecciona al menos un agente", reason="no_agents_selected"
            )
        if any(
            planned.get(item) is None or planned[item].kind != "agent"
            for item in selected_ids
        ):
            raise _invalid_directory(
                "La selección de agentes no es válida", reason="invalid_selection"
            )

        detected_by_id = {item.component_id: item for item in detected}
        needed = set(selected_ids)
        for agent_id in selected_ids:
            needed.update(
                reference.local_component_id
                for reference in planned[agent_id].references
                if reference.local_component_id
            )
        choices = {item.component_id: item for item in options.component_choices}
        ref_choices = {
            (item.agent_component_id, item.reference_key): item.resource_id
            for item in options.reference_choices
        }
        await self.agents._ensure_migrated()
        await self.skills._ensure_migrated()
        resource_ids: dict[str, str] = {}
        saved_resources: list[dict[str, Any]] = []
        saved_agents: list[dict[str, Any]] = []

        selected_components = [
            detected_by_id[item] for item in needed if item in detected_by_id
        ]
        async with open_db() as conn:
            async with conn.transaction():
                for component in order_by_dependencies(selected_components):
                    item = planned.get(component.component_id)
                    if not item or item.kind == "agent":
                        continue
                    kind = COMPONENT_TYPE_TO_RESOURCE_KIND[component.component_type]
                    choice = choices.get(component.component_id)
                    action = choice.action if choice else item.default_action
                    resource_id = (
                        choice.resource_id if choice else item.selected_existing_id
                    )
                    if action == "review":
                        raise _invalid_directory(
                            f"Revisa cómo relacionar {item.name}",
                            reason="unresolved_component",
                        )
                    if action == "skip":
                        continue
                    if action == "reuse":
                        if not resource_id or not catalog.contains(kind, resource_id):
                            raise _invalid_directory(
                                f"El recurso elegido para {item.name} no es accesible",
                                reason="invalid_resource",
                            )
                        resource_ids[component.component_id] = resource_id
                        saved_resources.append(
                            {
                                "component_id": component.component_id,
                                "resource_type": kind,
                                "resource_id": resource_id,
                                "action": "reused",
                            }
                        )
                        continue
                    if item.security_blocked:
                        raise _invalid_directory(
                            f"El componente {item.name} está bloqueado por seguridad",
                            reason="security_blocked",
                        )
                    created = await self._create_resource(component, ctx.group_id, conn)
                    resource_ids[component.component_id] = str(created["id"])
                    saved_resources.append(
                        {
                            "component_id": component.component_id,
                            "resource_type": kind,
                            "resource_id": str(created["id"]),
                            "action": "created",
                        }
                    )

                for agent_id in sorted(selected_ids):
                    item = planned[agent_id]
                    draft = item.agent
                    if draft is None:
                        continue
                    related: dict[str, list[str]] = {
                        "skills": [],
                        "knowledge": [],
                        "knowledge_packs": [],
                        "prompts": [],
                        "tools": [],
                    }
                    for reference in item.references:
                        selected_id = None
                        if reference.local_component_id:
                            selected_id = resource_ids.get(reference.local_component_id)
                        else:
                            selected_id = ref_choices.get(
                                (agent_id, reference.key), reference.selected_id
                            )
                            if selected_id and not catalog.contains(
                                reference.kind, selected_id
                            ):
                                raise _invalid_directory(
                                    f"La relación {reference.source} no es accesible",
                                    reason="invalid_resource",
                                )
                        if selected_id:
                            field = RESOURCE_KIND_TO_AGENT_FIELD[reference.kind]
                            if selected_id not in related[field]:
                                related[field].append(selected_id)
                    agent = await self.agents.save(
                        {
                            **draft.model_dump(exclude={"scope"}),
                            **related,
                            "labels": ["private", "community"],
                        },
                        scope="private",
                        owner_id=ctx.group_id,
                        conn=conn,
                        assume_new=True,
                    )
                    saved_agents.append(
                        {
                            "component_id": agent_id,
                            "resource_id": str(agent["id"]),
                            "name": str(agent["name"]),
                            "action": "created",
                        }
                    )

        return AgentDirectoryImportResult(
            agents=saved_agents,
            resources=saved_resources,
            agent_count=len(saved_agents),
            resource_count=len(saved_resources),
        )

    async def _create_resource(
        self, component: PackageComponent, owner_id: str, conn: Any
    ) -> dict[str, Any]:
        content = strip_frontmatter(component.content)
        labels = ["private", "community"]
        if component.component_type == "skill":
            return await self.skills.save(
                "private",
                {
                    "name": component.name,
                    "description": component.description,
                    "category": "dev",
                    "content": content,
                    "labels": labels,
                },
                owner_id=owner_id,
                conn=conn,
                assume_new=True,
            )
        if component.component_type in {"prompt", "command"}:
            alias = re.sub(r"[^a-z0-9_-]+", "-", component.component_id.lower()).strip(
                "-"
            )[:30]
            alias = alias if len(alias) >= 3 else f"imp-{alias}"[:30]
            return await self.prompts.save(
                "private",
                {
                    "name": component.name,
                    "description": component.description,
                    "alias": alias,
                    "content": content,
                    "labels": labels,
                },
                owner_id=owner_id,
                conn=conn,
                assume_new=True,
            )
        if component.component_type == "knowledge":
            return await self.knowledge.save(
                type="text",
                title=component.name,
                source=f"local-directory:{component.source_path}",
                content=content,
                owner_id=owner_id,
                labels=labels,
                conn=conn,
                assume_new=True,
            )
        if component.component_type == "tool":
            return await self.tools.save(
                "private",
                {
                    "name": component.name,
                    "description": component.description,
                    "language": component.tool_language,
                    "content": content,
                    "labels": labels,
                },
                owner_id=owner_id,
                conn=conn,
                assume_new=True,
            )
        raise _invalid_directory(
            "Tipo de componente no compatible", reason="unsupported_component"
        )


def uploads_from_parts(
    paths: Iterable[str], contents: Iterable[bytes]
) -> list[tuple[str, bytes]]:
    path_list = list(paths)
    content_list = list(contents)
    if not path_list:
        raise _invalid_directory("La carpeta está vacía", reason="empty")
    if len(path_list) != len(content_list):
        raise _invalid_directory(
            "La lista de rutas no coincide con los archivos",
            reason="path_count_mismatch",
        )
    return list(zip(path_list, content_list))
