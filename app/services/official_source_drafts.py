"""Borradores revisables para importar repositorios oficiales sin LLM."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from app.config.content_languages import CONTENT_LANGUAGE_LABELS
from app.models.official_source import MATERIALIZABLE_TYPES, PackageComponent
from app.services.official_source_importer import OfficialSourceImporter
from app.services.official_source_llm import OfficialSourceLLMAnalyzer
from app.services.official_source_sync import OfficialSourceMaterializer
from app.sql import sql
from app.storage.db import open_db
from app.storage.official_source_storage import OfficialSourceStorage
from app.storage.skill_storage import SKILL_LABELS
from app.storage.tool_storage import TOOL_LANGUAGES

_MANUAL_TYPES = frozenset(
    {"agent", "skill", "prompt", "knowledge", "tool", "memory", "workflow"}
)


def _tool_language(item: Dict[str, Any]) -> str:
    explicit = str(
        item.get("forced_tool_language") or item.get("tool_language") or ""
    )
    if explicit:
        return explicit
    return {
        ".py": "python",
        ".sh": "shell",
        ".cpp": "cpp",
    }.get(PurePosixPath(str(item.get("source_path") or "")).suffix.lower(), "")


class OfficialImportDraftService:
    def __init__(
        self,
        storage: Optional[OfficialSourceStorage] = None,
        importer: Optional[OfficialSourceImporter] = None,
        materializer: Optional[OfficialSourceMaterializer] = None,
        llm_analyzer: Optional[OfficialSourceLLMAnalyzer] = None,
    ) -> None:
        self.storage = storage or OfficialSourceStorage()
        self.importer = importer or OfficialSourceImporter(self.storage)
        self.materializer = materializer or OfficialSourceMaterializer(self.storage)
        self.llm_analyzer = llm_analyzer or OfficialSourceLLMAnalyzer()

    async def inspect(
        self,
        repository_url: str,
        owner_id: str,
        *,
        tracking_mode: str,
        tracking_ref: str,
        import_mode: str = "deterministic",
        llm_connection_id: str = "",
        progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        await self.storage.delete_expired_drafts()
        if progress:
            await progress({"stage": "downloading", "current": 0, "total": 0})
        snapshot = await self.importer.inspect_snapshot(
            repository_url,
            tracking_mode=tracking_mode,
            tracking_ref=tracking_ref,
        )
        if progress:
            await progress(
                {
                    "stage": "detecting",
                    "current": 0,
                    "total": 0,
                    "files": len(snapshot["files"]),
                }
            )
        deterministic = self.importer.analyze_snapshot(snapshot)
        if import_mode == "llm":
            if not llm_connection_id:
                raise ValueError("llm_connection_required")
            fetched = await self.llm_analyzer.analyze(
                snapshot,
                llm_connection_id,
                deterministic["components"],
                progress=progress,
            )
        elif import_mode == "deterministic":
            fetched = deterministic
            fetched["source"] = {
                **fetched["source"],
                "import_mode": "deterministic",
                "llm_connection_id": None,
            }
        else:
            raise ValueError("invalid_import_mode")
        source = dict(fetched["source"])
        source["resolved_version"] = fetched["version"]
        source["commit_sha"] = fetched["commit_sha"]
        components = [
            {
                **item.as_dict(include_content=True),
                "materializable": item.component_type in MATERIALIZABLE_TYPES,
                "selected": False,
                "explicitly_selected": False,
                "state": "new",
            }
            for item in fetched["components"]
        ]
        if progress:
            await progress(
                {
                    "stage": "saving_draft",
                    "current": len(components),
                    "total": len(components),
                    "files": len(snapshot["files"]),
                    "components": len(components),
                }
            )
        return await self.storage.create_draft(
            owner_id=owner_id,
            source=source,
            components=components,
            errors=fetched["errors"],
            security_warnings=fetched["security_warnings"],
        )

    async def inspect_source(self, source_id: str, owner_id: str) -> Dict[str, Any]:
        await self.storage.delete_expired_drafts()
        source = await self.storage.get_source(source_id)
        if not source:
            raise KeyError("source_not_found")
        if not source.get("owner_id"):
            existing_links = await self.storage.list_resources(source_id)
            if existing_links:
                raise ValueError("source_owner_required")
            await self.storage.set_owner(source_id, owner_id)
            source = await self.storage.get_source(source_id)
            assert source is not None
        snapshot = await self.importer.inspect_snapshot(
            str(source["repository_url"]),
            tracking_mode=str(source.get("tracking_mode") or "release"),
            tracking_ref=str(source.get("tracking_ref") or ""),
        )
        snapshot["source"] = {**source, **snapshot["source"], "id": source_id}
        deterministic = self.importer.analyze_snapshot(snapshot)
        if source.get("import_mode") == "llm":
            connection_id = str(source.get("llm_connection_id") or "")
            if not connection_id:
                raise ValueError("llm_connection_required")
            fetched = await self.llm_analyzer.analyze(
                snapshot, connection_id, deterministic["components"]
            )
        else:
            fetched = deterministic
        existing = await self.storage.list_resources(source_id)
        mappings = await self.storage.list_mappings(source_id)
        by_key = {str(item["component_id"]): item for item in existing}
        by_hash: Dict[str, List[Dict[str, Any]]] = {}
        for item in existing:
            if item.get("content_hash"):
                by_hash.setdefault(str(item["content_hash"]), []).append(item)

        seen: set[str] = set()
        components: List[Dict[str, Any]] = []
        for component in fetched["components"]:
            direct = by_key.get(component.component_id)
            if direct is None:
                matches = by_hash.get(component.content_hash, [])
                direct = matches[0] if len(matches) == 1 else None
                if direct:
                    component.component_id = str(direct["component_id"])
            mapping = mappings.get(component.source_path, {})
            selected = direct is not None
            state = (
                "new"
                if direct is None
                else "unchanged"
                if direct.get("content_hash") == component.content_hash
                else "updated"
            )
            payload = {
                **component.as_dict(include_content=True),
                "materializable": component.component_type in MATERIALIZABLE_TYPES,
                "selected": selected,
                "explicitly_selected": bool(
                    direct.get("explicitly_selected", True) if direct else False
                ),
                "forced_type": mapping.get("forced_type"),
                "forced_language": mapping.get("forced_language"),
                "forced_tool_language": mapping.get("forced_tool_language"),
                "state": state,
            }
            if mapping.get("dependencies"):
                payload["dependencies"] = list(mapping["dependencies"])
            components.append(payload)
            seen.add(component.component_id)

        for component_key, item in by_key.items():
            if component_key in seen:
                continue
            components.append(
                {
                    "source_id": source_id,
                    "component_id": component_key,
                    "component_type": item["resource_type"],
                    "name": component_key,
                    "description": "",
                    "source_path": item.get("source_path", ""),
                    "content_hash": item.get("content_hash", ""),
                    "labels": ["official"],
                    "dependencies": [],
                    "materializable": True,
                    "selected": False,
                    "explicitly_selected": False,
                    "state": "removed",
                }
            )
        draft_source = {
            **source,
            "resolved_version": fetched["version"],
            "commit_sha": fetched["commit_sha"],
            "base_commit_sha": source.get("last_commit_sha") or "",
        }
        return await self.storage.create_draft(
            owner_id=owner_id,
            source_id=source_id,
            source=draft_source,
            components=components,
            errors=fetched["errors"],
            security_warnings=fetched["security_warnings"],
        )

    async def update_component(
        self, draft_id: str, component_key: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        current = await self.storage.get_draft_component(draft_id, component_key)
        if not current:
            return None
        forced_type = updates.get("forced_type")
        if forced_type is not None and forced_type not in _MANUAL_TYPES:
            raise ValueError("invalid_forced_type")
        forced_language = updates.get("forced_language")
        if (
            forced_language not in {None, ""}
            and forced_language not in CONTENT_LANGUAGE_LABELS
        ):
            raise ValueError("invalid_forced_language")
        forced_tool_language = updates.get("forced_tool_language")
        if (
            forced_tool_language not in {None, ""}
            and forced_tool_language not in TOOL_LANGUAGES
        ):
            raise ValueError("invalid_forced_tool_language")
        await self.storage.update_draft_component(draft_id, component_key, updates)
        if "selected" not in updates and "dependencies" not in updates:
            return await self.storage.get_draft_component(draft_id, component_key)

        items = await self.storage.get_all_draft_components(draft_id)
        by_id = {str(item["component_id"]): item for item in items}
        explicit = {
            key for key, item in by_id.items() if item.get("explicitly_selected")
        }
        if "selected" in updates:
            selecting = bool(updates["selected"])
            if selecting:
                explicit.add(component_key)
            else:
                explicit.discard(component_key)
                # Desmarcar una dependencia elimina la selección de quienes la
                # necesitan. La cascada continúa hasta estabilizarse.
                changed = True
                blocked = {component_key}
                while changed:
                    changed = False
                    for key in list(explicit):
                        if blocked.intersection(by_id[key].get("dependencies", [])):
                            explicit.discard(key)
                            blocked.add(key)
                            changed = True
        selected = self._dependency_closure(explicit, by_id)
        await self.storage.replace_draft_selection(
            draft_id, selected=selected, explicit=explicit
        )
        return await self.storage.get_draft_component(draft_id, component_key)

    async def diff(self, draft_id: str) -> Dict[str, Any]:
        draft = await self.storage.get_draft(draft_id)
        if not draft:
            raise KeyError("draft_not_found")
        items = await self.storage.get_all_draft_components(draft_id)
        create = [item for item in items if item["selected"] and item["state"] == "new"]
        update = [
            item for item in items if item["selected"] and item["state"] == "updated"
        ]
        unchanged = [
            item for item in items if item["selected"] and item["state"] == "unchanged"
        ]
        delete = [
            item for item in items if not item["selected"] and item["state"] != "new"
        ]
        detached_references: List[Dict[str, str]] = []
        if draft.get("source_id"):
            links = {
                str(item["component_id"]): item
                for item in await self.storage.list_resources(str(draft["source_id"]))
            }
            async with open_db() as conn:
                agent_rows = await conn.fetchall(
                    sql("queries/official_sources:agents_not_from_source"),
                    (draft["source"].get("owner_id"), draft["source_id"]),
                )
            field_by_type = {
                "skill": "skills",
                "knowledge": "knowledge",
                "prompt": "prompts",
                "tool": "tools",
            }
            for item in delete:
                link = links.get(str(item["component_id"]))
                if not link:
                    continue
                resource_id = str(link["resource_id"])
                field = field_by_type.get(str(link["resource_type"]))
                for agent in agent_rows:
                    data = json.loads(agent["data"])
                    related = (
                        resource_id in {str(value) for value in data.get(field, [])}
                        if field
                        else str(link["resource_type"]) == "memory"
                        and str(data.get("memory_file") or "") == f"{resource_id}.md"
                    )
                    if related:
                        detached_references.append(
                            {
                                "agent_id": str(agent["id"]),
                                "agent_name": str(agent["name"]),
                                "component_id": str(item["component_id"]),
                            }
                        )
        warnings = list(draft["security_warnings"])
        if detached_references:
            warnings.append(
                "Se retirarán referencias desde agentes manuales conservados"
            )
        return {
            "draft_id": draft_id,
            "create": self._summaries(create),
            "update": self._summaries(update),
            "delete": self._summaries(delete),
            "unchanged": self._summaries(unchanged),
            "counts": {
                "create": len(create),
                "update": len(update),
                "delete": len(delete),
                "unchanged": len(unchanged),
            },
            "warnings": warnings,
            "detached_references": detached_references,
        }

    async def relations(self, draft_id: str) -> Dict[str, Any]:
        """Componentes del borrador y sus dependencias, en hechos planos.

        El grafo lo arma el cliente, igual que el del resto de recursos: aquí
        solo se dice qué componente trae el repositorio y cuál depende de cuál.
        """
        from app.services import resource_relations as relations_service

        draft = await self.storage.get_draft(draft_id)
        if not draft:
            raise KeyError("draft_not_found")
        components = await self.storage.get_all_draft_components(draft_id)

        items: List[Dict[str, Any]] = [
            relations_service.item(
                component.get("forced_type") or component["component_type"],
                component["component_id"],
                component["name"],
                description=(
                    f"{component['state']} · "
                    f"{'seleccionado' if component['selected'] else 'no seleccionado'}"
                ),
                relation="origin",
            )
            for component in components
        ]
        tipos = {
            component["component_id"]: (
                component.get("forced_type") or component["component_type"]
            )
            for component in components
        }
        for component in components:
            dependencies = component.get("dependencies", [])
            for dependency in dependencies:
                if dependency not in tipos:
                    continue
                items.append(
                    relations_service.item(
                        tipos[dependency],
                        dependency,
                        dependency,
                        relation="depends",
                        via=(tipos[component["component_id"]], component["component_id"]),
                    )
                )
            for relation in component.get("relations", []):
                target = relation.get("target_id")
                if not target or target in dependencies or target not in tipos:
                    continue
                items.append(
                    relations_service.item(
                        tipos[target],
                        target,
                        target,
                        relation="uses"
                        if relation.get("relation_type") == "uses"
                        else "shared",
                        via=(tipos[component["component_id"]], component["component_id"]),
                    )
                )

        return relations_service.payload(
            root_type="official_source",
            root_id=draft_id,
            root_label=draft["source"].get("name", "Repositorio"),
            root_description=draft["source"].get("repository_url", ""),
            items=items,
        )

    async def apply(self, draft_id: str, admin_id: str) -> Dict[str, Any]:
        draft = await self.storage.get_draft(draft_id)
        if not draft:
            raise KeyError("draft_not_found")
        if draft["expired"] or draft["status"] != "pending":
            raise ValueError("draft_not_applicable")
        if draft["owner_id"] != admin_id:
            raise PermissionError("draft_owner_mismatch")
        if draft["errors"]:
            raise ValueError("draft_has_errors")
        source = dict(draft["source"])
        source_id = draft.get("source_id")
        created_source = False
        items = await self.storage.get_all_draft_components(draft_id)
        selected = [
            item for item in items if item["selected"] and item["state"] != "removed"
        ]
        selected_ids = {str(item["component_id"]) for item in selected}
        for item in selected:
            effective_type = str(item.get("forced_type") or item["component_type"])
            if effective_type not in MATERIALIZABLE_TYPES:
                raise ValueError("selected_component_not_materializable")
            if item.get("security_blocked"):
                raise ValueError("selected_tool_security_blocked")
            if effective_type == "tool" and not item.get("security_accepted"):
                raise ValueError("selected_tool_requires_review")
            if effective_type == "tool" and str(
                _tool_language(item)
            ) not in TOOL_LANGUAGES:
                raise ValueError("selected_tool_requires_language")
            if set(item.get("dependencies", [])) - selected_ids:
                raise ValueError("selected_component_has_missing_dependencies")
        if source_id:
            current = await self.storage.get_source(str(source_id))
            if not current or not current.get("owner_id"):
                raise ValueError("source_owner_required")
            owner_id = str(current["owner_id"])
            source = {**current, **source, "id": str(source_id)}
        else:
            existing = await self.storage.find_by_repository(source["repository_url"])
            if existing:
                raise ValueError("repository_already_registered")
            source["owner_id"] = admin_id
            saved = await self.storage.save_source(source)
            source_id = str(saved["id"])
            source = saved
            owner_id = admin_id
            created_source = True

        source["commit_sha"] = str(draft["commit_sha"])
        components: List[PackageComponent] = []
        for item in selected:
            effective_type = str(item.get("forced_type") or item["component_type"])
            components.append(
                self._package_component(item, str(source_id), effective_type)
            )

        expected_commit = str(source.get("base_commit_sha") or "")
        if not await self.storage.acquire_sync_lock(str(source_id), expected_commit):
            current = await self.storage.get_source(str(source_id))
            if current and current.get("sync_state") == "applying":
                raise ValueError("source_sync_in_progress")
            raise ValueError("draft_outdated")
        try:
            result = await self.materializer.materialize(
                source,
                components,
                [component.component_id for component in components],
                owner_id=owner_id,
                explicit_component_ids=[
                    str(item["component_id"])
                    for item in selected
                    if item.get("explicitly_selected")
                ],
            )
            await self.storage.mark_sync(
                str(source_id),
                version=str(draft["resolved_version"]),
                commit_sha=str(draft["commit_sha"]),
                state="idle",
            )
            for item in items:
                if (
                    item.get("forced_type")
                    or item.get("forced_language")
                    or item.get("forced_tool_language")
                    or item.get("dependencies")
                ):
                    await self.storage.save_mapping(
                        str(source_id),
                        str(item.get("source_path") or ""),
                        forced_type=item.get("forced_type"),
                        forced_language=item.get("forced_language"),
                        forced_tool_language=item.get("forced_tool_language"),
                        dependencies=list(item.get("dependencies", [])),
                    )
            await self.storage.mark_draft_status(draft_id, "applied")
            return {**result, "draft_id": draft_id}
        except Exception as exc:
            if created_source:
                await self.materializer.delete_source(str(source_id))
            else:
                await self.storage.mark_sync(
                    str(source_id), error=str(exc), state="failed"
                )
            raise

    @staticmethod
    def _dependency_closure(
        explicit: Iterable[str], by_id: Dict[str, Dict[str, Any]]
    ) -> set[str]:
        selected = set(explicit)
        pending = list(selected)
        while pending:
            current = pending.pop()
            for dependency in by_id.get(current, {}).get("dependencies", []):
                if dependency in by_id and dependency not in selected:
                    selected.add(dependency)
                    pending.append(dependency)
        return selected

    @staticmethod
    def _summaries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "component_id": item["component_id"],
                "name": item["name"],
                "component_type": item.get("forced_type") or item["component_type"],
                "source_path": item.get("source_path", ""),
            }
            for item in items
        ]

    @staticmethod
    def _package_component(
        item: Dict[str, Any], source_id: str, component_type: str
    ) -> PackageComponent:
        labels = [
            label for label in item.get("labels", ["official"]) if label in SKILL_LABELS
        ]
        language = str(item.get("forced_language") or item.get("language") or "")
        tool_language = _tool_language(item)
        if language and language not in labels:
            labels.append(language)
        return PackageComponent(
            source_id=source_id,
            component_id=str(item["component_id"]),
            component_type=component_type,
            name=str(item["name"]),
            description=str(item.get("description") or ""),
            source_path=str(item.get("source_path") or ""),
            content_hash=str(item.get("content_hash") or ""),
            content=str(item.get("content") or ""),
            files=dict(item.get("files") or {}),
            labels=labels,
            dependencies=list(item.get("dependencies") or []),
            relations=list(item.get("relations") or []),
            language=language,
            tool_language=tool_language,
            detected_by=str(item.get("detected_by") or "generic"),
            variants=list(item.get("variants") or []),
            executable=bool(item.get("executable")),
            security_blocked=bool(item.get("security_blocked")),
            security_review_required=bool(item.get("security_review_required")),
        )
