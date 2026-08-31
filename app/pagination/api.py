"""Contrato HTTP v2, tipado y autocontenido, para páginas cursor."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

from app.pagination.models import CursorPage

T = TypeVar("T")


class CursorPageMetadata(BaseModel):
    limit: int
    has_more: bool
    next_cursor: str | None = None
    total: int | None = None
    snapshot_at: str | None = None


class CursorPageResponse(BaseModel, Generic[T]):
    items: list[T]
    page: CursorPageMetadata

    @classmethod
    def from_result(
        cls, result: CursorPage[T], *, limit: int
    ) -> "CursorPageResponse[T]":
        return cls(
            items=list(result.items),
            page=CursorPageMetadata(
                limit=limit,
                has_more=result.has_more,
                next_cursor=result.next_cursor,
                total=result.total,
                snapshot_at=result.snapshot_at,
            ),
        )
