"""Materializa el contenido de una fuente oficial como recursos normales.

El catálogo oficial no es un tipo de objeto aparte: lo que trae una fuente se
guarda en las mismas tablas que cualquier agente o skill de usuario, con la
label ``official`` y las columnas ``official_source_id`` /
``official_component_id`` para saber de dónde salió. Todo lo demás —enlazar,
forkear, exportar, buscar— sigue el camino común de la aplicación.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

from app.config import data as _cfg
from app.models.official_source import MATERIALIZABLE_TYPES, PackageComponent
from app.storage import db as _db
from app.storage.agent_storage import AgentStorage
from app.storage.db import AsyncConn, open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.official_source_storage import (
    OFFICIAL_RESOURCE_TABLES,
    OfficialSourceStorage,
)
from app.storage.prompt_storage import PromptStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage

# Lo oficial nace público: es la razón de traerlo. El resto de labels del
# componente se respetan salvo las que decide el sistema.
_SYSTEM_LABELS = frozenset(
    {"private", "public", "official", "community", "fork", "linked"}
)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "component"


def _strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    return parts[2].lstrip() if len(parts) == 3 else content


def select_components(
    components: Sequence[PackageComponent], component_ids: Optional[Iterable[str]]
) -> List[PackageComponent]:
    """Selección con el cierre transitivo de dependencias, en orden estable.

    ``None`` es "todo" —lo que hace una primera importación—; una lista vacía
    es "nada", que es lo que pide quien desmarca todas las casillas. Confundir
    ambos casos instalaba el repositorio entero al intentar vaciarlo.
    """
    if component_ids is None:
        return list(components)
    selected = {str(item) for item in component_ids if str(item)}
    if not selected:
        return []
    by_id = {component.component_id: component for component in components}
    missing = selected - by_id.keys()
    if missing:
        raise ValueError(f"Componentes no encontrados: {', '.join(sorted(missing))}")
    pending = list(selected)
    while pending:
        current = pending.pop()
        for dependency in by_id[current].dependencies:
            if dependency not in by_id:
                raise ValueError(f"Dependencia no encontrada: {dependency}")
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return [item for item in components if item.component_id in selected]


def order_by_dependencies(
    components: Sequence[PackageComponent],
) -> List[PackageComponent]:
    """Las dependencias primero: un agente necesita el id real de sus skills."""
    pending = list(components)
    ordered: List[PackageComponent] = []
    resolved: set[str] = set()
    while pending:
        progressed = False
        for component in list(pending):
            if set(component.dependencies).issubset(resolved):
                ordered.append(component)
                resolved.add(component.component_id)
                pending.remove(component)
                progressed = True
        if not progressed:
            # Defensivo: validate_components rechaza los ciclos antes de llegar
            # aquí, pero un manifiesto histórico no debe bloquear el sync.
            ordered.extend(pending)
            break
    return ordered


class OfficialSourceMaterializer:
    def __init__(self, storage: Optional[OfficialSourceStorage] = None) -> None:
        self.storage = storage or OfficialSourceStorage()
        self.agents = AgentStorage(_cfg.AGENTS_DIR)
        self.skills = SkillStorage(_cfg.SKILLS_DIR)
        self.knowledge = KnowledgeStorage()
        self.memory = MemoryStorage(_cfg.MEMORY_DIR)
        self.prompts = PromptStorage()
        self.tools = ToolStorage()
        self.workflows = WorkflowStorage()
        self.versions = ResourceVersionStorage()

    async def materialize(
        self,
        source: Dict[str, Any],
        components: Sequence[PackageComponent],
        component_ids: Optional[Iterable[str]],
        owner_id: str,
        *,
        explicit_component_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Deja los recursos de la fuente exactamente en la selección dada.

        Lo marcado se crea o se actualiza; lo que deja de estarlo se borra,
        como se borraría cualquier recurso.
        """
        source_id = str(source["id"])
        selected = select_components(components, component_ids)
        keep = {component.component_id for component in selected}
        explicit = (
            {str(item) for item in explicit_component_ids}
            if explicit_component_ids is not None
            else set(keep)
        )

        await self.agents._ensure_migrated()
        await self.skills._ensure_migrated()
        await self.memory._ensure_migrated()
        existing_by_component = {
            str(item["component_id"]): item
            for item in await self.storage.list_resources(source_id)
        }
        resource_ids: Dict[str, str] = {}
        resource_types: Dict[str, str] = {}
        saved: List[Dict[str, Any]] = []
        async with open_db() as conn:
            async with conn.transaction():
                for component in selected:
                    existing = existing_by_component.get(component.component_id)
                    if existing:
                        await self._snapshot_before_overwrite(
                            existing, owner_id, conn=conn
                        )
                removed = await self._remove_unselected(source_id, keep, conn=conn)
                for component in order_by_dependencies(selected):
                    if component.component_type not in MATERIALIZABLE_TYPES:
                        continue
                    existing = existing_by_component.get(component.component_id)
                    resource = await self._save(
                        component,
                        owner_id,
                        existing,
                        resource_ids,
                        resource_types,
                        conn=conn,
                    )
                    if not resource:
                        continue
                    resource_type, resource_id = resource
                    resource_ids[component.component_id] = resource_id
                    resource_types[component.component_id] = resource_type
                    await self.storage.mark_resource(
                        resource_type,
                        resource_id,
                        owner_id,
                        source_id=source_id,
                        component_id=component.component_id,
                        source_path=component.source_path,
                        content_hash=component.content_hash,
                        commit_sha=str(
                            source.get("commit_sha")
                            or source.get("last_commit_sha")
                            or ""
                        ),
                        explicitly_selected=component.component_id in explicit,
                        conn=conn,
                    )
                    await self._publish(
                        resource_type,
                        resource_id,
                        owner_id,
                        component,
                        source,
                        conn=conn,
                    )
                    saved.append(
                        {
                            "component_id": component.component_id,
                            "resource_type": resource_type,
                            "resource_id": resource_id,
                            "name": component.name,
                        }
                    )
        return {
            "source_id": source_id,
            "resources": saved,
            "removed": removed,
        }

    async def remove_all(self, source_id: str) -> int:
        """Borra todo lo que trajo una fuente. Se usa al eliminarla."""
        async with open_db() as conn:
            async with conn.transaction():
                return await self._remove_unselected(source_id, keep=set(), conn=conn)

    async def delete_source(self, source_id: str) -> int:
        """Borra fuente y objetos originales en la misma transacción."""
        async with open_db() as conn:
            async with conn.transaction():
                removed = await self._remove_unselected(
                    source_id, keep=set(), conn=conn
                )
                await conn.execute(
                    "DELETE FROM official_sources WHERE id=?", (source_id,)
                )
                return removed

    # ── interno ──────────────────────────────────────────────────────────

    async def _remove_unselected(
        self, source_id: str, keep: set[str], *, conn: AsyncConn
    ) -> int:
        removed = 0
        for item in await self.storage.list_resources(source_id):
            if str(item["component_id"] or "") in keep:
                continue
            # La relación es la autoridad de procedencia y debe desaparecer
            # aunque el borrado físico del recurso falle o el recurso ya no
            # exista. Las columnas antiguas se limpian en la misma operación.
            await self.storage.mark_resource(
                str(item["resource_type"]),
                str(item["resource_id"]),
                str(item["owner_id"]),
                source_id=None,
                conn=conn,
            )
            await self._delete(
                str(item["resource_type"]),
                str(item["resource_id"]),
                str(item["owner_id"]),
                conn=conn,
            )
            removed += 1
        return removed

    async def _delete(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        *,
        conn: AsyncConn,
    ) -> None:
        agent_field = {
            "skill": "skills",
            "knowledge": "knowledge",
            "prompt": "prompts",
            "tool": "tools",
        }.get(resource_type)
        if agent_field or resource_type == "memory":
            agent_rows = await conn.fetchall(
                "SELECT id,owner_id,data FROM agents WHERE owner_id=?", (owner_id,)
            )
            for row in agent_rows:
                data = json.loads(row["data"])
                changed = False
                if agent_field:
                    current = [str(item) for item in data.get(agent_field, [])]
                    if resource_id in current:
                        data[agent_field] = [
                            item for item in current if item != resource_id
                        ]
                        changed = True
                elif str(data.get("memory_file") or "") == f"{resource_id}.md":
                    data["use_memory"] = False
                    data["memory_file"] = None
                    changed = True
                if changed:
                    await conn.execute(
                        "UPDATE agents SET data=? WHERE id=? AND owner_id=?",
                        (
                            json.dumps(data, ensure_ascii=False),
                            row["id"],
                            row["owner_id"],
                        ),
                    )
        table = {
            "agent": "agents",
            "skill": "skills",
            "knowledge": "knowledge_items",
            "prompt": "prompts",
            "tool": "tools",
            "workflow": "agent_workflows",
            "memory": "memory_files",
        }.get(resource_type)
        if table:
            await conn.execute(
                f"DELETE FROM {table} WHERE id=? AND owner_id=?",
                (resource_id, owner_id),
            )
        await conn.execute(
            "DELETE FROM resource_labels WHERE resource_type=? AND resource_id=? "
            "AND owner_id=?",
            (resource_type, resource_id, owner_id),
        )
        await conn.execute(
            "DELETE FROM resource_social WHERE resource_type=? AND resource_id=? "
            "AND owner=?",
            (resource_type, resource_id, owner_id),
        )
        await conn.execute(
            "DELETE FROM resource_group_shares WHERE resource_type=? AND resource_id=? "
            "AND shared_by=?",
            (resource_type, resource_id, owner_id),
        )
        await conn.execute(
            "DELETE FROM resource_versions "
            "WHERE resource_type=? AND resource_id=? AND owner_id=?",
            (resource_type, resource_id, owner_id),
        )

    def _labels(self, component: PackageComponent) -> List[str]:
        extra = [label for label in component.labels if label not in _SYSTEM_LABELS]
        return ["public", "official", *extra]

    async def _save(
        self,
        component: PackageComponent,
        owner_id: str,
        existing: Optional[Dict[str, Any]],
        resource_ids: Dict[str, str],
        resource_types: Dict[str, str],
        *,
        conn: AsyncConn,
    ) -> Optional[tuple[str, str]]:
        """Crea o actualiza el recurso del componente; devuelve (tipo, id)."""
        labels = self._labels(component)
        content = _strip_frontmatter(component.content)
        reuse_id = str((existing or {}).get("resource_id") or "")
        kind = component.component_type

        if kind == "skill":
            payload = {
                "name": component.name,
                "description": component.description,
                "category": "dev",
                "content": content,
                "labels": labels,
            }
            if reuse_id:
                payload["id"] = reuse_id
            resource = await self.skills.save(
                "private", payload, owner_id=owner_id, conn=conn
            )
            return "skill", str(resource["id"])

        if kind in ("prompt", "command"):
            alias = _slug(f"{component.component_id}-{component.source_id[:8]}")[:30]
            if len(alias) < 3:
                alias = f"official-{alias}"[:30]
            payload = {
                "name": component.name,
                "description": component.description,
                "alias": alias,
                "content": content,
                "labels": labels,
            }
            if reuse_id:
                payload["id"] = reuse_id
            resource = await self.prompts.save(
                "private", payload, owner_id=owner_id, conn=conn
            )
            return "prompt", str(resource["id"])

        if kind == "tool":
            language = {".py": "python", ".sh": "shell"}.get(
                PurePosixPath(component.source_path).suffix
            )
            if not language:
                return None
            payload = {
                "name": component.name,
                "description": component.description,
                "language": language,
                "content": content,
                "labels": labels,
            }
            if reuse_id:
                payload["id"] = reuse_id
            resource = await self.tools.save(
                "private", payload, owner_id=owner_id, conn=conn
            )
            return "tool", str(resource["id"])

        if kind == "knowledge":
            resource = await self.knowledge.save(
                type="text",
                title=component.name,
                source=f"official:{component.source_id}:{component.component_id}",
                content=content,
                owner_id=owner_id,
                labels=labels,
                item_id=reuse_id or None,
                conn=conn,
            )
            return "knowledge", str(resource["id"])

        if kind == "memory":
            filename = f"{reuse_id or _slug(component.name)}.md"
            resource = await self.memory.save(
                filename, content, owner_id=owner_id, conn=conn
            )
            return "memory", str(resource["id"])

        if kind == "agent":
            related: Dict[str, List[str]] = {
                "skills": [],
                "knowledge": [],
                "prompts": [],
                "tools": [],
            }
            memory_file: Optional[str] = None
            for dependency in component.dependencies:
                resource_id = resource_ids.get(dependency)
                if not resource_id:
                    continue
                field = {
                    "skill": "skills",
                    "knowledge": "knowledge",
                    "prompt": "prompts",
                    "tool": "tools",
                }.get(resource_types.get(dependency, ""))
                if field:
                    related[field].append(resource_id)
                elif resource_types.get(dependency) == "memory":
                    memory_file = f"{resource_id}.md"
            payload = {
                "name": component.name,
                "description": component.description,
                "system_prompt": content,
                "labels": labels,
                "use_memory": memory_file is not None,
                "memory_file": memory_file,
                **related,
            }
            if reuse_id:
                payload["id"] = reuse_id
            resource = await self.agents.save(
                payload, scope="private", owner_id=owner_id, conn=conn
            )
            return "agent", str(resource["id"])

        if kind == "workflow":
            try:
                definition = yaml.safe_load(component.content) or {}
            except yaml.YAMLError:
                definition = {}
            if not isinstance(definition, dict):
                return None
            payload = {
                "name": component.name,
                "description": component.description,
                "definition": definition,
                "scope": "private",
                "labels": labels,
            }
            if reuse_id:
                payload["id"] = reuse_id
            resource = await self.workflows.save(owner_id, payload, conn=conn)
            return "workflow", str(resource["id"])

        return None

    async def _snapshot_before_overwrite(
        self,
        existing: Dict[str, Any],
        owner_id: str,
        *,
        conn: AsyncConn,
    ) -> None:
        resource_type = str(existing["resource_type"])
        resource_id = str(existing["resource_id"])
        snapshot: Optional[Dict[str, Any]] = None
        if resource_type == "agent":
            snapshot = await self.agents.get(resource_id, owner_id=owner_id)
        elif resource_type == "skill":
            snapshot = await self.skills.get_any(resource_id, owner_id=owner_id)
        elif resource_type == "prompt":
            snapshot = await self.prompts.get_any(resource_id, owner_id=owner_id)
        elif resource_type == "tool":
            snapshot = await self.tools.get_any(resource_id, owner_id=owner_id)
        elif resource_type == "knowledge":
            snapshot = await self.knowledge.get(resource_id, owner_id)
        elif resource_type == "workflow":
            snapshot = await self.workflows.get(resource_id, owner_id)
        elif resource_type == "memory":
            content = await self.memory.get(resource_id, owner_id)
            if content is not None:
                snapshot = {"id": resource_id, "content": content}
        if snapshot:
            await self.versions.create(
                resource_type,
                resource_id,
                owner_id,
                snapshot,
                created_by=owner_id,
                reason="official_source_sync",
                conn=conn,
            )

    async def _publish(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        component: PackageComponent,
        source: Dict[str, Any],
        *,
        conn: AsyncConn,
    ) -> None:
        """Fila pública en resource_social: es lo que lo hace visible en Explorar."""
        labels = json.dumps(self._labels(component), ensure_ascii=False)
        columns = (
            "resource_type, resource_id, owner, name, description, is_public, "
            "category, trial_missing_deps, tags, labels"
        )
        values = "?, ?, ?, ?, ?, 1, '', 'warn', '[]', ?"
        conflict = (
            "ON CONFLICT (resource_type, resource_id, owner) DO UPDATE SET "
            "name=EXCLUDED.name, description=EXCLUDED.description, is_public=1, "
            "labels=EXCLUDED.labels"
            if _db.IS_PG
            else "ON CONFLICT (resource_type, resource_id, owner) DO UPDATE SET "
            "name=excluded.name, description=excluded.description, is_public=1, "
            "labels=excluded.labels"
        )
        await conn.execute(
            f"INSERT INTO resource_social ({columns}) VALUES ({values}) {conflict}",
            (
                resource_type,
                resource_id,
                owner_id,
                component.name,
                component.description or str(source.get("name") or ""),
                labels,
            ),
        )


__all__ = [
    "OFFICIAL_RESOURCE_TABLES",
    "OfficialSourceMaterializer",
    "order_by_dependencies",
    "select_components",
]
