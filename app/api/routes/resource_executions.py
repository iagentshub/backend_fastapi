"""Estado visible de ejecuciones activas para cualquier cliente."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.routes.auth import GroupContext, require_group_session
from app.storage.resource_executions import ResourceExecutionStorage

router = APIRouter(prefix="/api/resource-executions", tags=["resource-executions"])
_executions = ResourceExecutionStorage()


class ResourceExecutionState(BaseModel):
    execution_id: str
    resource_type: Literal["agent", "workflow"]
    resource_id: str
    resource_ids: list[str]
    run_id: str | None = None
    status: Literal["in_progress"]
    started_at: str


@router.get("")
async def list_resource_executions(
    ctx: GroupContext = Depends(require_group_session),
) -> list[ResourceExecutionState]:
    return await _executions.list_for_user(ctx.user, ctx.group_id)
