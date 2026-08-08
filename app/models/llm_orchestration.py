"""Modelo de una orquestación de conexiones LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

from app.models.base import BaseResource


@dataclass(kw_only=True)
class LLMOrchestration(BaseResource):
    resource_type: ClassVar[str] = "llm_orchestration"
    mode: str = "stack"
    candidates: List[Dict[str, str]] = field(default_factory=list)
    router_connection_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMOrchestration":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "").strip(),
            description=str(data.get("description") or "").strip(),
            mode=str(data.get("mode") or "stack"),
            candidates=[
                {
                    "connection_id": str(item.get("connection_id") or ""),
                    "routing_hint": str(item.get("routing_hint") or ""),
                }
                for item in (data.get("candidates") or [])
                if isinstance(item, dict)
            ],
            router_connection_id=str(data.get("router_connection_id") or "").strip()
            or None,
            labels=[str(value) for value in (data.get("labels") or ["private"])],
            scope="private",
            owner_id=str(data.get("owner_id") or "") or None,
            is_active=bool(data.get("is_active", True)),
            deactivated_at=str(data.get("deactivated_at") or "") or None,
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "resource_type": self.resource_type,
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "candidates": self.candidates,
            "router_connection_id": self.router_connection_id,
            "labels": self.labels,
            "scope": "private",
            "owner_id": self.owner_id,
            "is_active": self.is_active,
            "deactivated_at": self.deactivated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
