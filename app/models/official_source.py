"""Modelos de dominio de las fuentes oficiales.

Lo que una fuente trae no es un tipo de objeto aparte: se materializa como
recurso normal (agente, skill, prompt, tool, knowledge, workflow) marcado con
``official_source_id``. Aquí solo viven la fuente y el componente detectado en
el repositorio, que es material en tránsito entre GitHub y los storages de
recurso — nunca se persiste tal cual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

COMPONENT_TYPES = frozenset(
    {
        "skill",
        "agent",
        "knowledge",
        "prompt",
        "workflow",
        "rule",
        "command",
        "hook",
        "mcp",
        "tool",
    }
)

# Tipos que tienen un storage de recurso detrás. El resto (hook, mcp, rule) se
# detectan para poder informar de ellos, pero no se materializan.
#
# "command" entra porque un comando de barra es un prompt con nombre: son la
# misma cosa con dos nombres según el IDE, y dejarlo fuera hacía que un
# repositorio como caveman perdiera cinco objetos sin explicación.
MATERIALIZABLE_TYPES = frozenset(
    {"skill", "agent", "knowledge", "prompt", "command", "workflow", "tool"}
)

# Fuente interna para lo que un admin marca como oficial a mano, sin que venga
# de ningún repositorio.
INTERNAL_SOURCE_ID = "official_by_iagentshub"


@dataclass(kw_only=True)
class PackageComponent:
    source_id: str
    component_id: str
    component_type: str
    name: str
    source_path: str
    content_hash: str
    description: str = ""
    content: str = ""
    files: Dict[str, str] = field(default_factory=dict)
    labels: List[str] = field(default_factory=lambda: ["official"])
    dependencies: List[str] = field(default_factory=list)

    def as_dict(self, *, include_content: bool = False) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "source_id": self.source_id,
            "component_id": self.component_id,
            "component_type": self.component_type,
            "name": self.name,
            "description": self.description,
            "source_path": self.source_path,
            "labels": self.labels,
            "dependencies": self.dependencies,
            "content_hash": self.content_hash,
        }
        if include_content:
            result["content"] = self.content
            result["files"] = self.files
        return result


@dataclass(kw_only=True)
class OfficialSource:
    resource_type: ClassVar[str] = "official_source"

    id: str
    name: str
    repository_url: str
    repository_owner: str = ""
    repository_name: str = ""
    description: str = ""
    tracking_mode: str = "release"
    tracking_ref: str = "main"
    license: str = ""
    last_version: Optional[str] = None
    latest_checked_at: Optional[str] = None
    last_sync_error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "resource_type": self.resource_type,
            "name": self.name,
            "description": self.description,
            "repository_url": self.repository_url,
            "repository_owner": self.repository_owner,
            "repository_name": self.repository_name,
            "tracking_mode": self.tracking_mode,
            "tracking_ref": self.tracking_ref,
            "license": self.license,
            "last_version": self.last_version,
            "latest_checked_at": self.latest_checked_at,
            "last_sync_error": self.last_sync_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
