"""Modelo de dominio de un elemento de conocimiento."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict

from app.models.base import BaseResource

_OWN_KEYS = {
    "id", "name", "description", "icon", "owner_id", "created_by", "scope",
    "labels", "is_active", "deactivated_at", "created_at", "updated_at",
    "resource_type", "title", "type", "source", "content", "char_count",
    "source_char_count", "content_truncated", "truncation_reason",
}


@dataclass(kw_only=True)
class KnowledgeItem(BaseResource):
    resource_type: ClassVar[str] = "knowledge"

    #: Tipo de origen: "url" o "document"
    type: str = "document"
    source: str = ""
    content: str = ""
    char_count: int = 0

    #: Caracteres del original y si la extracción se dejó algo fuera. Van en el
    #: modelo, no en `extra`, porque la interfaz tiene que poder avisar: un
    #: documento a medias que se enseña como entero es la forma en que esto
    #: pasaba desapercibido.
    source_char_count: int = 0
    content_truncated: bool = False
    truncation_reason: str = ""

    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeItem":
        # Compatibilidad: la tabla usa "title"; "name" es el campo canónico nuevo
        name = str(data.get("name") or data.get("title") or "").strip()
        return cls(
            id=str(data.get("id") or ""),
            name=name,
            description=str(data.get("description") or "").strip(),
            icon=str(data.get("icon") or "").strip(),
            type=str(data.get("type") or "document").strip(),
            source=str(data.get("source") or "").strip(),
            content=str(data.get("content") or ""),
            char_count=int(data.get("char_count") or 0),
            source_char_count=int(data.get("source_char_count") or 0),
            content_truncated=bool(data.get("content_truncated") or False),
            truncation_reason=str(data.get("truncation_reason") or ""),
            labels=[str(lbl) for lbl in (data.get("labels") or []) if lbl],
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
            "title": self.name,  # aditivo: los clientes existentes leen "title"
            "resource_type": self.resource_type,
            "description": self.description,
            "icon": self.icon,
            "type": self.type,
            "source": self.source,
            "content": self.content,
            "char_count": self.char_count,
            "source_char_count": self.source_char_count,
            "content_truncated": self.content_truncated,
            "truncation_reason": self.truncation_reason,
            "labels": self.labels,
            "scope": self.scope,
            "owner_id": self.owner_id,
            "created_by": self.created_by,
            "is_active": self.is_active,
            "deactivated_at": self.deactivated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
