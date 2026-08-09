"""Preferencias de conexión por usuario y agente."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.routes.auth import require_auth
from app.storage.db import IS_PG, PH, open_db

router = APIRouter(prefix="/api/agents", tags=["agent-preferences"])


class AgentPreferenceBody(BaseModel):
    connection_id: str | None = None


@router.get("/{agent_id}/preferences")
async def get_agent_preferences(
    agent_id: str,
    user: str = Depends(require_auth),
) -> dict[str, Any]:
    async with open_db() as conn:
        row = await conn.fetchone(
            f"SELECT connection_id FROM user_agent_preferences "
            f"WHERE username={PH} AND agent_id={PH}",
            (user, agent_id),
        )
    return {"connection_id": row["connection_id"] if row else None}


@router.put("/{agent_id}/preferences")
async def put_agent_preferences(
    agent_id: str,
    body: AgentPreferenceBody,
    user: str = Depends(require_auth),
) -> dict[str, Any]:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                f"INSERT INTO user_agent_preferences "
                f"(username, agent_id, connection_id, updated_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}) "
                f"ON CONFLICT (username, agent_id) DO UPDATE SET "
                f"connection_id=EXCLUDED.connection_id, "
                f"updated_at=EXCLUDED.updated_at",
                (user, agent_id, body.connection_id, now_str),
            )
        else:
            await conn.execute(
                f"INSERT OR REPLACE INTO user_agent_preferences "
                f"(username, agent_id, connection_id, updated_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH})",
                (user, agent_id, body.connection_id, now_str),
            )
        await conn.commit()
    return {"ok": True}
