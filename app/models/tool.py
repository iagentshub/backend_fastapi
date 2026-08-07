"""Modelo de dominio de una tool (herramienta ejecutable)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

from app.models.base import BaseResource

_OWN_KEYS = {
    "id", "name", "description", "icon", "owner_id", "created_by", "scope",
    "labels", "is_active", "deactivated_at", "created_at", "updated_at",
    "resource_type", "language", "content",
    "binary_filename", "binary_size", "binary_uploaded_at",
}


@dataclass(kw_only=True)
class Tool(BaseResource):
    resource_type: ClassVar[str] = "tool"

    language: str = ""
    content: str = ""
    #: Metadatos ligeros del binario subido (solo para language="cpp").
    #: El contenido en base64 (binary_b64) NO es un campo del dataclass —
    #: se maneja como columna SQL cruda fuera del modelo de dominio, igual
    #: que "avatar" en la tabla users no forma parte de ningún dataclass User.
    binary_filename: Optional[str] = None
    binary_size: Optional[int] = None
    binary_uploaded_at: Optional[str] = None
    labels: List[str] = field(default_factory=lambda: ["private"])

    #: Claves no declaradas — conservadas para round-trip fiel del payload
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tool":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "").strip(),
            description=str(data.get("description") or "").strip(),
            icon=str(data.get("icon") or "").strip(),
            language=str(data.get("language") or "").strip(),
            content=str(data.get("content") or ""),
            binary_filename=str(data.get("binary_filename") or "").strip() or None,
            binary_size=int(data["binary_size"])
            if data.get("binary_size") is not None
            else None,
            binary_uploaded_at=str(data.get("binary_uploaded_at") or "") or None,
            labels=[str(lbl) for lbl in (data.get("labels") or ["private"]) if lbl],
            scope=data.get("scope") or "private",  # type: ignore[arg-type]
            owner_id=str(data["owner_id"]).strip() or None
            if data.get("owner_id")
            else None,
            created_by=str(data["created_by"]).strip() or None
            if data.get("created_by")
            else None,
            is_active=bool(data.get("is_active", True)),
            deactivated_at=str(data.get("deactivated_at") or "") or None,
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            extra={k: v for k, v in data.items() if k not in _OWN_KEYS},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.extra,
            "id": self.id,
            "name": self.name,
            "resource_type": self.resource_type,
            "description": self.description,
            "icon": self.icon,
            "language": self.language,
            "content": self.content,
            "binary_filename": self.binary_filename,
            "binary_size": self.binary_size,
            "binary_uploaded_at": self.binary_uploaded_at,
            "labels": self.labels,
            "scope": self.scope,
            "owner_id": self.owner_id,
            "created_by": self.created_by,
            "is_active": self.is_active,
            "deactivated_at": self.deactivated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
