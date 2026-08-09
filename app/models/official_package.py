"""Modelos de dominio del catálogo oficial de paquetes open source."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

PACKAGE_STATUSES = frozenset(
    {"draft", "pending_review", "published", "rejected", "superseded"}
)
COMPONENT_TYPES = frozenset(
    {"skill", "agent", "rule", "command", "hook", "mcp", "tool"}
)
EXPORT_TARGETS = frozenset({"hub", "codex", "claude", "cursor"})


@dataclass(kw_only=True)
class PackageComponent:
    package_id: str
    version: str
    component_id: str
    component_type: str
    name: str
    source_path: str
    content_hash: str
    description: str = ""
    content: str = ""
    files: Dict[str, str] = field(default_factory=dict)
    targets: List[str] = field(default_factory=list)

    def as_dict(self, *, include_content: bool = False) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "package_id": self.package_id,
            "version": self.version,
            "component_id": self.component_id,
            "component_type": self.component_type,
            "name": self.name,
            "description": self.description,
            "source_path": self.source_path,
            "targets": self.targets,
            "content_hash": self.content_hash,
        }
        if include_content:
            result["content"] = self.content
            result["files"] = self.files
        return result


@dataclass(kw_only=True)
class PackageVersion:
    package_id: str
    version: str
    commit_sha: str
    status: str
    manifest: Dict[str, Any] = field(default_factory=dict)
    validation_errors: List[str] = field(default_factory=list)
    created_at: str = ""
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    components: List[PackageComponent] = field(default_factory=list)


@dataclass(kw_only=True)
class OfficialPackage:
    resource_type: ClassVar[str] = "official_package"

    id: str
    name: str
    repository_url: str
    repository_owner: str
    repository_name: str
    description: str = ""
    tracking_mode: str = "release"
    tracking_ref: str = "main"
    license: str = ""
    published_version: Optional[str] = None
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
            "published_version": self.published_version,
            "latest_checked_at": self.latest_checked_at,
            "last_sync_error": self.last_sync_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_official": bool(self.published_version),
        }
