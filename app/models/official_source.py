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

from pydantic import BaseModel, Field

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
        "memory",
        "unknown",
    }
)

# Tipos que tienen un storage de recurso detrás. El resto (hook, mcp, rule) se
# detectan para poder informar de ellos, pero no se materializan.
#
# "command" entra porque un comando de barra es un prompt con nombre: son la
# misma cosa con dos nombres según el IDE, y dejarlo fuera hacía que un
# repositorio como caveman perdiera cinco objetos sin explicación.
MATERIALIZABLE_TYPES = frozenset(
    {"skill", "agent", "knowledge", "prompt", "command", "workflow", "tool", "memory"}
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
    language: str = ""
    detected_by: str = "generic"
    variants: List[str] = field(default_factory=list)
    executable: bool = False
    security_blocked: bool = False
    security_review_required: bool = False

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
            "language": self.language,
            "detected_by": self.detected_by,
            "variants": self.variants,
            "executable": self.executable,
            "security_blocked": self.security_blocked,
            "security_review_required": self.security_review_required,
        }
        if include_content:
            result["content"] = self.content
            result["files"] = self.files
        return result


class OfficialSource(BaseModel):
    resource_type: ClassVar[str] = "official_source"

    id: str
    name: str
    repository_url: str
    repository_owner: str = ""
    repository_name: str = ""
    provider: str = "github"
    repository_path: str = ""
    owner_id: Optional[str] = None
    default_branch: str = "main"
    description: str = ""
    tracking_mode: str = "release"
    tracking_ref: str = "main"
    license: str = ""
    last_version: Optional[str] = None
    last_commit_sha: Optional[str] = None
    sync_state: str = "idle"
    latest_checked_at: Optional[str] = None
    last_sync_error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {**self.model_dump(), "resource_type": self.resource_type}


class ImportComponent(BaseModel):
    component_id: str
    component_type: str
    name: str
    source_path: str
    state: str = "new"
    selected: bool = False
    explicitly_selected: bool = False
    materializable: bool = False
    description: str = ""
    content_hash: str = ""
    dependencies: List[str] = Field(default_factory=list)
    variants: List[str] = Field(default_factory=list)
    forced_type: Optional[str] = None
    forced_language: Optional[str] = None
    security_accepted: bool = False
    security_blocked: bool = False
    security_review_required: bool = False


class ImportDraft(BaseModel):
    id: str
    source_id: Optional[str] = None
    owner_id: str
    source: Dict[str, Any]
    status: str = "pending"
    expires_at: str
    expired: bool = False
    errors: List[Any] = Field(default_factory=list)
    security_warnings: List[Any] = Field(default_factory=list)
    component_count: int = 0


class ImportDiff(BaseModel):
    draft_id: str
    create: List[Dict[str, Any]] = Field(default_factory=list)
    update: List[Dict[str, Any]] = Field(default_factory=list)
    delete: List[Dict[str, Any]] = Field(default_factory=list)
    unchanged: List[Dict[str, Any]] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    warnings: List[Any] = Field(default_factory=list)


class OriginInfo(BaseModel):
    source_id: str
    component_key: str
    resource_type: str
    resource_id: str
    resource_owner_id: str
    source_name: str
    repository_url: str
    provider: str
    repository_path: str
    source_path: str = ""
    content_hash: str = ""
    commit_sha: str = ""
