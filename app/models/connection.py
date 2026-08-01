"""Modelo de dominio de una conexión a proveedor LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict

from app.models.base import BaseResource

_OWN_KEYS = {
    "id", "name", "description", "icon", "owner_id", "created_by", "scope",
    "labels", "is_active", "deactivated_at", "created_at", "updated_at",
    "resource_type", "type", "model", "host", "url", "tokens_in", "tokens_out",
}


@dataclass(kw_only=True)
class Connection(BaseResource):
    resource_type: ClassVar[str] = "connection"

    #: Proveedor (openai, anthropic, ollama, ssh, database…)
    type: str = ""
    model: str = ""
    host: str = ""
    url: str = ""
    tokens_in: int = 0
    tokens_out: int = 0

    #: Credenciales y campos específicos del proveedor (api_key, password,
    #: ssh_key…) — conservados para round-trip fiel; el cifrado lo aplica
    #: el storage, no el modelo.
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Connection":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "").strip(),
            description=str(data.get("description") or "").strip(),
            icon=str(data.get("icon") or "").strip(),
            type=str(data.get("type") or "").strip(),
            model=str(data.get("model") or "").strip(),
            host=str(data.get("host") or "").strip(),
            url=str(data.get("url") or "").strip(),
            tokens_in=int(data.get("tokens_in") or 0),
            tokens_out=int(data.get("tokens_out") or 0),
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
            "type": self.type,
            "model": self.model,
            "host": self.host,
            "url": self.url,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "labels": self.labels,
            "scope": self.scope,
            "owner_id": self.owner_id,
            "created_by": self.created_by,
            "is_active": self.is_active,
            "deactivated_at": self.deactivated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
