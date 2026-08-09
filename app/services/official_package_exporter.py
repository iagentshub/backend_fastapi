"""Plan único de exportación para ZIP y para la extensión de VS Code."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

import yaml

from app.config import data as _cfg
from app.storage import db as _db
from app.storage.agent_storage import AgentStorage
from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.official_package_storage import OfficialPackageStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage

_TARGETS = frozenset({"codex", "claude", "cursor"})


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "component"


def _strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    return parts[2].lstrip() if len(parts) == 3 else content


def _component_root(target: str, component: Dict[str, Any]) -> str:
    component_id = _slug(str(component["component_id"]))
    kind = component["component_type"]
    roots = {
        "codex": {
            "skill": f".agents/skills/{component_id}",
            "agent": ".codex/agents",
            "knowledge": ".codex/knowledge",
            "prompt": ".codex/prompts",
            "workflow": ".codex/workflows",
            "rule": ".codex/rules",
            "command": ".codex/prompts",
            "hook": ".codex/hooks",
            "mcp": ".codex/mcp",
            "tool": ".codex/tools",
        },
        "claude": {
            "skill": f".claude/skills/{component_id}",
            "agent": ".claude/agents",
            "knowledge": ".claude/knowledge",
            "prompt": ".claude/commands",
            "workflow": ".claude/workflows",
            "rule": ".claude/rules",
            "command": ".claude/commands",
            "hook": ".claude/hooks",
            "mcp": ".claude/mcp",
            "tool": ".claude/tools",
        },
        "cursor": {
            "skill": f".cursor/skills/{component_id}",
            "agent": ".cursor/agents",
            "knowledge": ".cursor/knowledge",
            "prompt": ".cursor/commands",
            "workflow": ".cursor/workflows",
            "rule": ".cursor/rules",
            "command": ".cursor/commands",
            "hook": ".cursor/hooks",
            "mcp": ".cursor/mcp",
            "tool": ".cursor/tools",
        },
    }
    return roots[target][kind]


def _selected_components(
    components: List[Dict[str, Any]], component_ids: Optional[Iterable[str]]
) -> List[Dict[str, Any]]:
    """Resolve the transitive dependency closure in stable package order."""
    selected = {str(item) for item in (component_ids or []) if str(item)}
    if not selected:
        return components
    by_id = {str(item["component_id"]): item for item in components}
    missing = selected - by_id.keys()
    if missing:
        raise ValueError(f"Componentes no encontrados: {', '.join(sorted(missing))}")
    pending = list(selected)
    while pending:
        current = pending.pop()
        for dependency in by_id[current].get("dependencies") or []:
            dependency_id = str(dependency)
            if dependency_id not in by_id:
                raise ValueError(f"Dependencia no encontrada: {dependency_id}")
            if dependency_id not in selected:
                selected.add(dependency_id)
                pending.append(dependency_id)
    return [item for item in components if str(item["component_id"]) in selected]


def _existing_copy_payload(copy: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": copy["id"],
        "source_package_id": copy["package_id"],
        "source_version": copy["source_version"],
        "source_component_id": copy["component_id"],
        "resource_type": copy["resource_type"],
        "resource_id": copy.get("resource_id"),
        "name": copy["name"],
        "content_hash": copy["source_content_hash"],
        "status": "Sin cambios",
        "is_official": False,
    }


def build_export_plan(
    package: Dict[str, Any], target: str, component_ids: Optional[Iterable[str]] = None
) -> Dict[str, Any]:
    if target not in _TARGETS:
        raise ValueError("Destino de exportación no válido")
    version = package["version"]
    components = _selected_components(version.get("components", []), component_ids)
    files: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for component in components:
        if target not in component.get("targets", []):
            warnings.append(
                f"{component['name']} no declara compatibilidad con {target}"
            )
            continue
        root = _component_root(target, component)
        source_name = PurePosixPath(str(component["source_path"])).name
        if component["component_type"] == "skill":
            filename = "SKILL.md"
        elif source_name:
            filename = source_name
        else:
            filename = f"{_slug(component['name'])}.md"
        path = f"{root}/{filename}"
        files.append(
            {
                "path": path,
                "content": component.get("content", ""),
                "content_hash": component["content_hash"],
                "component_id": component["component_id"],
            }
        )
        for relative, content in sorted((component.get("files") or {}).items()):
            safe = PurePosixPath(relative)
            if safe.is_absolute() or ".." in safe.parts:
                warnings.append(f"Archivo auxiliar inseguro omitido: {relative}")
                continue
            files.append(
                {
                    "path": f"{root}/{safe.as_posix()}",
                    "content": content,
                    "component_id": component["component_id"],
                }
            )
    install_manifest = {
        "schema_version": 1,
        "package_id": package["id"],
        "package_name": package["name"],
        "source_version": version["version"],
        "target": target,
        "files": [item["path"] for item in files],
    }
    files.append(
        {
            "path": f".iagentshub/official-packages/{package['id']}.json",
            "content": json.dumps(install_manifest, indent=2, ensure_ascii=False)
            + "\n",
            "component_id": "_manifest",
        }
    )
    return {
        "package_id": package["id"],
        "name": package["name"],
        "version": version["version"],
        "target": target,
        "is_official": True,
        "files": files,
        "warnings": warnings,
    }


def export_plan_zip(plan: Dict[str, Any]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in plan["files"]:
            archive.writestr(item["path"], item["content"])
    return output.getvalue()


class OfficialPackageCopier:
    def __init__(self, storage: Optional[OfficialPackageStorage] = None) -> None:
        self.storage = storage or OfficialPackageStorage()
        self.skills = SkillStorage(_cfg.SKILLS_DIR)
        self.agents = AgentStorage(_cfg.AGENTS_DIR)
        self.knowledge = KnowledgeStorage()
        self.prompts = PromptStorage()
        self.tools = ToolStorage()
        self.workflows = WorkflowStorage()

    async def copy(
        self,
        package_id: str,
        owner_id: str,
        component_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        return await self._materialize(
            package_id, owner_id, component_ids, mode="copy"
        )

    async def link(
        self,
        package_id: str,
        owner_id: str,
        component_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        return await self._materialize(
            package_id, owner_id, component_ids, mode="link"
        )

    async def _materialize(
        self,
        package_id: str,
        owner_id: str,
        component_ids: Optional[Iterable[str]],
        *,
        mode: str,
    ) -> Dict[str, Any]:
        package = await self.storage.get_published(package_id, include_content=True)
        if not package:
            raise KeyError("package_not_found")
        components = _selected_components(
            package["version"]["components"], component_ids
        )
        copies: List[Dict[str, Any]] = []
        existing: Dict[str, Dict[str, Any]] = {}
        for item in await self.storage.list_copies(owner_id, mode=mode):
            if (
                item["package_id"] == package_id
                and item["source_version"] == package["version"]["version"]
                and await self._copy_still_exists(item, owner_id)
            ):
                existing[str(item["component_id"])] = item
        resource_ids: Dict[str, str] = {
            component_id: str(item["resource_id"])
            for component_id, item in existing.items()
            if item.get("resource_id")
        }
        # Dependencies are materialised before their consumers, independent of
        # the order in which files happened to be discovered in the source.
        pending = list(components)
        ordered: List[Dict[str, Any]] = []
        ordered_ids: set[str] = set()
        while pending:
            progressed = False
            for component in list(pending):
                dependencies = {
                    str(item) for item in component.get("dependencies") or []
                }
                if dependencies.issubset(ordered_ids):
                    ordered.append(component)
                    ordered_ids.add(str(component["component_id"]))
                    pending.remove(component)
                    progressed = True
            if not progressed:
                # Defensive fallback for historical manifests. New versions
                # with cycles are rejected before publication.
                ordered.extend(pending)
                break

        for component in ordered:
            component_id = str(component["component_id"])
            if component_id in existing:
                copies.append(_existing_copy_payload(existing[component_id]))
                continue
            resource_type = "package_component"
            resource_id: Optional[str] = None
            labels = (
                ["private", "official", "linked"]
                if mode == "link"
                else ["private", "community", "fork"]
            )
            labels.extend(
                label
                for label in (component.get("labels") or [])
                if label not in {
                    "private",
                    "public",
                    "fork",
                    "linked",
                    "official",
                    "community",
                }
                and label not in labels
            )
            if component["component_type"] == "skill":
                resource = await self.skills.save(
                    "private",
                    {
                        "name": component["name"],
                        "description": component.get("description", ""),
                        "category": "dev",
                        "content": _strip_frontmatter(component.get("content", "")),
                        "labels": labels,
                    },
                    owner_id=owner_id,
                )
                resource_type, resource_id = "skill", resource["id"]
            elif component["component_type"] == "agent":
                related: Dict[str, List[str]] = {
                    "skills": [],
                    "knowledge": [],
                    "prompts": [],
                    "tools": [],
                }
                by_id = {str(item["component_id"]): item for item in components}
                for dependency in component.get("dependencies") or []:
                    dependency_id = str(dependency)
                    dependency_component = by_id.get(dependency_id) or {}
                    kind = str(dependency_component.get("component_type") or "")
                    field = {
                        "skill": "skills",
                        "knowledge": "knowledge",
                        "prompt": "prompts",
                        "tool": "tools",
                    }.get(kind)
                    copied_id = resource_ids.get(dependency_id)
                    if field and copied_id:
                        related[field].append(copied_id)
                resource = await self.agents.save(
                    {
                        "name": component["name"],
                        "description": component.get("description", ""),
                        "system_prompt": _strip_frontmatter(
                            component.get("content", "")
                        ),
                        "labels": labels,
                        **related,
                    },
                    scope="private",
                    owner_id=owner_id,
                )
                resource_type, resource_id = "agent", resource["id"]
            elif component["component_type"] == "knowledge":
                resource = await self.knowledge.save(
                    type="text",
                    title=component["name"],
                    source=f"official:{package_id}:{component['component_id']}",
                    content=_strip_frontmatter(component.get("content", "")),
                    owner_id=owner_id,
                    labels=labels,
                )
                resource_type, resource_id = "knowledge", resource["id"]
            elif component["component_type"] == "prompt":
                alias = _slug(f"{component['component_id']}-{package_id[:8]}")[:30]
                if len(alias) < 3:
                    alias = f"official-{alias}"[:30]
                resource = await self.prompts.save(
                    "private",
                    {
                        "name": component["name"],
                        "description": component.get("description", ""),
                        "alias": alias,
                        "content": _strip_frontmatter(component.get("content", "")),
                        "labels": labels,
                    },
                    owner_id=owner_id,
                )
                resource_type, resource_id = "prompt", resource["id"]
            elif component["component_type"] == "tool":
                suffix = PurePosixPath(str(component.get("source_path") or "")).suffix
                language = {".py": "python", ".sh": "shell"}.get(suffix)
                if language:
                    resource = await self.tools.save(
                        "private",
                        {
                            "name": component["name"],
                            "description": component.get("description", ""),
                            "language": language,
                            "content": _strip_frontmatter(component.get("content", "")),
                            "labels": labels,
                        },
                        owner_id=owner_id,
                    )
                    resource_type, resource_id = "tool", resource["id"]
            elif component["component_type"] == "workflow":
                try:
                    definition = yaml.safe_load(component.get("content", "")) or {}
                except yaml.YAMLError:
                    definition = {}
                if isinstance(definition, dict):
                    resource = await self.workflows.save(
                        owner_id,
                        {
                            "name": component["name"],
                            "description": component.get("description", ""),
                            "definition": definition,
                            "scope": "private",
                            "labels": labels,
                        },
                    )
                    resource_type, resource_id = "workflow", resource["id"]
            if resource_id:
                resource_ids[str(component["component_id"])] = str(resource_id)
            copy = await self.storage.save_copy(
                {
                    "owner_id": owner_id,
                    "package_id": package_id,
                    "source_version": package["version"]["version"],
                    "component_id": component["component_id"],
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "name": component["name"],
                    "content": component.get("content", ""),
                    "source_content_hash": component["content_hash"],
                    "mode": mode,
                }
            )
            copies.append(copy)
            if mode == "link" and resource_id:
                await self._register_official_link(
                    owner_id=owner_id,
                    package=package,
                    component=component,
                    resource_type=resource_type,
                    resource_id=str(resource_id),
                    labels=labels,
                )
        return {
            "package_id": package_id,
            "source_version": package["version"]["version"],
            "is_official": mode == "link",
            "links" if mode == "link" else "copies": copies,
        }

    async def _register_official_link(
        self,
        *,
        owner_id: str,
        package: Dict[str, Any],
        component: Dict[str, Any],
        resource_type: str,
        resource_id: str,
        labels: List[str],
    ) -> None:
        columns = (
            "resource_type, resource_id, owner, name, description, is_public, "
            "category, trial_missing_deps, linked_to_user, linked_to_id, tags, labels"
        )
        values = "?, ?, ?, ?, ?, 0, '', '', ?, ?, '[]', ?"
        sql = (
            f"INSERT INTO resource_social ({columns}) VALUES ({values}) "
            "ON CONFLICT DO NOTHING"
            if _db.IS_PG
            else f"INSERT OR IGNORE INTO resource_social ({columns}) VALUES ({values})"
        )
        async with open_db() as conn:
            await conn.execute(
                sql,
                (
                    resource_type,
                    resource_id,
                    owner_id,
                    component["name"],
                    component.get("description", ""),
                    package.get("repository_owner", "official"),
                    f"{package['id']}:{component['component_id']}",
                    json.dumps(labels, ensure_ascii=False),
                ),
            )
            await conn.commit()

    async def _copy_still_exists(self, copy: Dict[str, Any], owner_id: str) -> bool:
        resource_id = copy.get("resource_id")
        resource_type = str(copy.get("resource_type") or "")
        if not resource_id:
            return resource_type == "package_component"
        if resource_type == "agent":
            resource = await self.agents.get(str(resource_id), scope="private")
            return bool(resource and resource.get("owner_id") == owner_id)
        if resource_type == "skill":
            return bool(await self.skills.get("private", str(resource_id), owner_id))
        if resource_type == "knowledge":
            return bool(await self.knowledge.get(str(resource_id), owner_id))
        if resource_type == "prompt":
            return bool(await self.prompts.get("private", str(resource_id), owner_id))
        if resource_type == "tool":
            return bool(await self.tools.get("private", str(resource_id), owner_id))
        if resource_type == "workflow":
            return bool(await self.workflows.get(str(resource_id), owner_id))
        return False

    async def list_for_user(self, owner_id: str) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for copy in await self.storage.list_copies(owner_id):
            modified = await self._is_modified(copy, owner_id)
            package = await self.storage.get_package(str(copy["package_id"]))
            update_available = bool(
                package
                and package.get("published_version")
                and package["published_version"] != copy["source_version"]
            )
            status = (
                "Modificado"
                if modified
                else ("Actualización disponible" if update_available else "Sin cambios")
            )
            result.append(
                {
                    "id": copy["id"],
                    "source_package_id": copy["package_id"],
                    "source_package_name": (package or {}).get("name", ""),
                    "source_version": copy["source_version"],
                    "source_component_id": copy["component_id"],
                    "resource_type": copy["resource_type"],
                    "resource_id": copy.get("resource_id"),
                    "name": copy["name"],
                    "content_hash": copy["source_content_hash"],
                    "status": status,
                    "is_official": False,
                }
            )
        return result

    async def _is_modified(self, copy: Dict[str, Any], owner_id: str) -> bool:
        resource_id = copy.get("resource_id")
        expected = _strip_frontmatter(str(copy.get("content") or ""))
        if copy["resource_type"] == "skill" and resource_id:
            resource = await self.skills.get("private", str(resource_id), owner_id)
            return (
                not resource or resource.get("content", "").strip() != expected.strip()
            )
        if copy["resource_type"] == "agent" and resource_id:
            resource = await self.agents.get(str(resource_id), scope="private")
            return (
                not resource
                or resource.get("owner_id") != owner_id
                or resource.get("system_prompt", "").strip() != expected.strip()
            )
        return False
