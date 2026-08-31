"""Adaptadores reutilizables de paginación para recursos con scope."""

from __future__ import annotations

from typing import Any

from app.pagination.models import CursorPage, CursorParams
from app.services.resource_visibility import VisibilityFilter
from app.storage.scoped_resource_page import (
    ScopedResourcePageSpec,
    list_scoped_resource_page,
)


class ScopedResourcePaginationMixin:
    """Comparte el listado cursor de recursos con scope."""

    def _page_spec(self) -> ScopedResourcePageSpec:
        raise NotImplementedError

    async def list_visible_page(
        self,
        *,
        user: str,
        active_group_id: str,
        scope: str,
        page: CursorParams,
        requested_group_id: str | None = None,
        catalog_filter: VisibilityFilter | None = None,
    ) -> CursorPage[dict[str, Any]]:
        await self._ensure_migrated()  # type: ignore[attr-defined]
        return await list_scoped_resource_page(
            self._page_spec(),
            user=user,
            active_group_id=active_group_id,
            scope=scope,
            include_inactive=None,
            page=page,
            requested_group_id=requested_group_id,
            extra_filters=(catalog_filter,) if catalog_filter else (),
        )
