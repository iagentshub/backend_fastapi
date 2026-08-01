"""Rutas de etiquetas — enlaces entre objetos de cualquier tipo por etiqueta."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.api.routes.auth import GroupContext, require_group
from app.auth.auth import get_user_role
from app.storage.labels import resources_with_label

router = APIRouter(prefix="/api/labels", tags=["labels"])


@router.get("/{label}")
async def list_resources_with_label(
    label: str, ctx: GroupContext = Depends(require_group)
) -> List[Dict[str, Any]]:
    """Devuelve los recursos (de cualquier tipo) que llevan una etiqueta.

    El admin ve todos; el resto solo los suyos (por owner_id de group activo).
    """
    owner_id = None if await get_user_role(ctx.user) == "admin" else ctx.group_id
    return await resources_with_label(label, owner_id=owner_id)
