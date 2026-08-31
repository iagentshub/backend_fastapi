"""Modelos puros de paginación, sin dependencias de FastAPI ni de la BD."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CursorParams:
    """Página keyset solicitada mediante un cursor opaco opcional."""

    limit: int
    cursor: str | None = None
    include_total: bool = False
    consistent: bool = True

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit debe ser mayor que cero")


@dataclass(frozen=True, slots=True)
class CursorPosition:
    """Posición estable para órdenes temporales con desempate por ID."""

    created_at: str
    item_id: str
    snapshot_at: str | None = None
    total: int | None = None
    page_number: int = 1


@dataclass(frozen=True, slots=True)
class CursorPage(Generic[T]):
    """Resultado de una consulta keyset; el cursor apunta a la página siguiente."""

    items: Sequence[T]
    next_cursor: str | None
    has_more: bool
    total: int | None = None
    snapshot_at: str | None = None
