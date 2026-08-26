"""Short-lived shared staging for reviewed local-directory imports."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Sequence

from app.api.routes.auth import GroupContext
from app.config import data as _cfg
from app.errors import APIError
from app.models.agent_import import AgentDirectoryImportPlan
from app.models.official_source import PackageComponent
from app.utils.generators import generate_id

_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
_TTL_SECONDS = 30 * 60


def _root() -> Path:
    return _cfg.DATA_DIR / "agent_import_sessions"


def _invalid_session() -> APIError:
    return APIError(
        422,
        "invalid_field",
        "La vista previa ha caducado; vuelve a seleccionar el directorio",
        extra={"field": "session_id", "reason": "expired_import_session"},
    )


def _plan_payload(plan: AgentDirectoryImportPlan) -> dict:
    payload = plan.model_dump(exclude={"session_id"})
    by_id = {item.component_id: item for item in plan.components}
    for component in payload["components"]:
        internal = by_id[component["component_id"]]
        component["content_hash"] = internal.content_hash
        component["agent"] = internal.agent.model_dump() if internal.agent else None
    return payload


async def create_import_session(
    plan: AgentDirectoryImportPlan,
    detected: Sequence[PackageComponent],
    ctx: GroupContext,
) -> str:
    session_id = generate_id(32)
    payload = {
        "user": ctx.user,
        "group_id": ctx.group_id,
        "created_at": time.time(),
        "plan": _plan_payload(plan),
        "detected": [item.as_dict(include_content=True) for item in detected],
    }

    def write() -> None:
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / f".{session_id}.tmp"
        target = root / f"{session_id}.json"
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        cutoff = time.time() - _TTL_SECONDS
        for pattern in ("*.json", ".*.claimed", ".*.tmp"):
            for candidate in root.glob(pattern):
                try:
                    if candidate.stat().st_mtime < cutoff:
                        candidate.unlink()
                except OSError:
                    continue

    await asyncio.to_thread(write)
    return session_id


@asynccontextmanager
async def claim_import_session(
    session_id: str, ctx: GroupContext
) -> AsyncIterator[tuple[AgentDirectoryImportPlan, list[PackageComponent]]]:
    if not _SESSION_ID.fullmatch(session_id):
        raise _invalid_session()
    target = _root() / f"{session_id}.json"
    claimed = _root() / f".{session_id}.{generate_id(32)}.claimed"

    def claim() -> dict:
        try:
            os.replace(target, claimed)
            payload = json.loads(claimed.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            claimed.unlink(missing_ok=True)
            raise _invalid_session() from None
        if (
            payload.get("user") != ctx.user
            or payload.get("group_id") != ctx.group_id
            or float(payload.get("created_at") or 0) < time.time() - _TTL_SECONDS
        ):
            claimed.unlink(missing_ok=True)
            raise _invalid_session()
        return payload

    payload = await asyncio.to_thread(claim)
    succeeded = False
    try:
        yield (
            AgentDirectoryImportPlan.model_validate(payload["plan"]),
            [PackageComponent(**item) for item in payload["detected"]],
        )
        succeeded = True
    finally:
        def release() -> None:
            if succeeded:
                claimed.unlink(missing_ok=True)
            elif claimed.exists():
                os.replace(claimed, target)

        await asyncio.to_thread(release)
