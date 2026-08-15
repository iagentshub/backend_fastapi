"""Contratos compartidos de paginación para API, servicios y storage."""

from app.pagination.models import CursorPage, CursorPosition, OffsetPage, OffsetParams

__all__ = ["CursorPage", "CursorPosition", "OffsetPage", "OffsetParams"]
