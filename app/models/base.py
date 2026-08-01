"""Jerarquía base de todos los objetos del sistema.

Dos niveles:

- ``BaseEntity`` — todo lo que tiene id: identidad única, fechas y borrado
  suave (``is_active``/``deactivated_at``).
- ``BaseResource`` — recursos gestionados por el usuario (agentes, skills,
  conexiones, conocimientos, workflows…): añade nombre, propietario, ámbito
  y etiquetas.

Los ids son siempre alfanuméricos únicos generados con
``app.utils.generators.generate_id`` — nunca derivados del nombre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, List, Literal, Optional


@dataclass(kw_only=True)
class BaseEntity:
    """Base de todo objeto identificable del sistema."""

    id: str
    is_active: bool = True
    deactivated_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(kw_only=True)
class BaseResource(BaseEntity):
    """Base de los recursos que el usuario crea y gestiona."""

    name: str
    description: str = ""
    icon: str = ""
    owner_id: Optional[str] = None
    created_by: Optional[str] = None
    scope: Literal["public", "private"] = "private"
    labels: List[str] = field(default_factory=list)

    #: Discriminador por clase (no por instancia) — valor del registro
    #: app.models.resource_types.RESOURCE_TYPES.
    resource_type: ClassVar[str] = "resource"
