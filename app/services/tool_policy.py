"""One security/readiness policy for every Tool distribution boundary."""

from __future__ import annotations

from typing import Any, Mapping

from app.errors import APIError

TOOL_SECURITY_LABELS = frozenset({"review", "quarantine"})
TOOL_REVIEW_LABEL = "review"
TOOL_QUARANTINE_LABEL = "quarantine"


def tool_security_labels(tool: Mapping[str, Any]) -> set[str]:
    return TOOL_SECURITY_LABELS & {
        str(label) for label in (tool.get("labels") or []) if label
    }


def tool_is_ready(tool: Mapping[str, Any]) -> bool:
    if isinstance(tool.get("ready"), bool):
        return bool(tool["ready"])
    language = str(tool.get("language") or "")
    if language == "cpp":
        return bool(tool.get("binary_filename"))
    return bool(str(tool.get("content") or "").strip())


def tool_is_owner(tool: Mapping[str, Any], *, user_id: str, group_id: str) -> bool:
    return tool.get("owner_id") in {user_id, group_id}


def assert_tool_distributable(tool: Mapping[str, Any]) -> None:
    blocked = tool_security_labels(tool)
    if blocked:
        raise APIError(
            403,
            "forbidden",
            "La Tool no se puede distribuir mientras esté pendiente de revisión",
            extra={"resource": "tool", "labels": sorted(blocked)},
        )
    if not tool_is_ready(tool):
        raise APIError(
            409,
            "invalid_field",
            "La Tool no tiene una implementación lista",
            extra={"resource": "tool", "field": "implementation"},
        )


def assert_tool_consumable(
    tool: Mapping[str, Any], *, user_id: str, group_id: str, is_admin: bool
) -> None:
    if not tool.get("is_active", True):
        raise APIError(
            409,
            "resource_inactive",
            "La Tool está desactivada",
            extra={"resource": "tool", "resource_id": tool.get("id")},
        )
    if not tool_is_ready(tool):
        raise APIError(
            409,
            "invalid_field",
            "La Tool no tiene una implementación lista",
            extra={"resource": "tool", "field": "implementation"},
        )
    blocked = tool_security_labels(tool)
    if TOOL_QUARANTINE_LABEL in blocked:
        raise APIError(
            403,
            "forbidden",
            "La Tool está en cuarentena y no se puede utilizar",
            extra={"resource": "tool", "labels": [TOOL_QUARANTINE_LABEL]},
        )
    # Un administrador puede inspeccionar el código desde Admin, pero no debe
    # inyectar una Tool pendiente en un chat como si fuese su propietario.
    owner = tool_is_owner(tool, user_id=user_id, group_id=group_id)
    if TOOL_REVIEW_LABEL in blocked and not owner:
        assert_tool_distributable(tool)
    if not owner and not is_admin:
        assert_tool_distributable(tool)
