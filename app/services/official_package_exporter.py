"""Plan único de exportación para ZIP y para la extensión de VS Code."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from app.config import data as _cfg
from app.storage.agent_storage import AgentStorage
from app.storage.official_package_storage import OfficialPackageStorage
from app.storage.skill_storage import SkillStorage

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
            "rule": ".codex/rules",
            "command": ".codex/prompts",
            "hook": ".codex/hooks",
            "mcp": ".codex/mcp",
            "tool": ".codex/tools",
        },
        "claude": {
            "skill": f".claude/skills/{component_id}",
            "agent": ".claude/agents",
            "rule": ".claude/rules",
            "command": ".claude/commands",
            "hook": ".claude/hooks",
            "mcp": ".claude/mcp",
            "tool": ".claude/tools",
        },
        "cursor": {
            "skill": f".cursor/skills/{component_id}",
            "agent": ".cursor/agents",
            "rule": ".cursor/rules",
            "command": ".cursor/commands",
            "hook": ".cursor/hooks",
            "mcp": ".cursor/mcp",
            "tool": ".cursor/tools",
        },
    }
    return roots[target][kind]


def build_export_plan(
    package: Dict[str, Any], target: str, component_ids: Optional[Iterable[str]] = None
) -> Dict[str, Any]:
    if target not in _TARGETS:
        raise ValueError("Destino de exportación no válido")
    version = package["version"]
    selected = set(component_ids or [])
    files: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for component in version.get("components", []):
        if selected and component["component_id"] not in selected:
            continue
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
            "content": json.dumps(install_manifest, indent=2, ensure_ascii=False) + "\n",
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

    async def copy(
        self, package_id: str, owner_id: str, component_ids: Optional[Iterable[str]] = None
    ) -> Dict[str, Any]:
        package = await self.storage.get_published(package_id, include_content=True)
        if not package:
            raise KeyError("package_not_found")
        selected = set(component_ids or [])
        copies: List[Dict[str, Any]] = []
        for component in package["version"]["components"]:
            if selected and component["component_id"] not in selected:
                continue
            resource_type = "package_component"
            resource_id: Optional[str] = None
            if component["component_type"] == "skill":
                resource = await self.skills.save(
                    "private",
                    {
                        "name": component["name"],
                        "description": component.get("description", ""),
                        "category": "dev",
                        "content": _strip_frontmatter(component.get("content", "")),
                        "labels": ["private", "fork"],
                    },
                    owner_id=owner_id,
                )
                resource_type, resource_id = "skill", resource["id"]
            elif component["component_type"] == "agent":
                resource = await self.agents.save(
                    {
                        "name": component["name"],
                        "description": component.get("description", ""),
                        "system_prompt": _strip_frontmatter(component.get("content", "")),
                        "labels": ["private", "fork"],
                    },
                    scope="private",
                    owner_id=owner_id,
                )
                resource_type, resource_id = "agent", resource["id"]
            copies.append(
                await self.storage.save_copy(
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
                    }
                )
            )
        return {
            "package_id": package_id,
            "source_version": package["version"]["version"],
            "is_official": False,
            "copies": copies,
        }

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
            status = "Modificado" if modified else (
                "Actualización disponible" if update_available else "Sin cambios"
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
            return not resource or resource.get("content", "").strip() != expected.strip()
        if copy["resource_type"] == "agent" and resource_id:
            resource = await self.agents.get(str(resource_id), scope="private")
            return (
                not resource
                or resource.get("owner_id") != owner_id
                or resource.get("system_prompt", "").strip() != expected.strip()
            )
        return False
