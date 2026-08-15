"""Modelos puros de paginación, sin dependencias de FastAPI ni de la BD."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class OffsetParams:
    """Página solicitada mediante límite y desplazamiento."""

    limit: int
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit debe ser mayor que cero")
        if self.offset < 0:
            raise ValueError("offset no puede ser negativo")


@dataclass(frozen=True, slots=True)
class OffsetPage(Generic[T]):
    """Resultado de una consulta paginada con total exacto."""

    items: Sequence[T]
    total: int
    params: OffsetParams

    @property
    def has_more(self) -> bool:
        return self.params.offset + len(self.items) < self.total


@dataclass(frozen=True, slots=True)
class CursorPosition:
    """Posición estable para órdenes temporales con desempate por ID."""

    created_at: str
    item_id: str


@dataclass(frozen=True, slots=True)
class CursorPage(Generic[T]):
    """Resultado de una consulta keyset; el cursor apunta a la página siguiente."""

    items: Sequence[T]
    next_cursor: str | None
    has_more: bool
