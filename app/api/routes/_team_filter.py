"""Helpers para enforcement de permisos de equipo."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_team_allowed_ids(username: str, resource_type: str, perm: str) -> Optional[List[str]]:
    """Return allowed resource IDs for a team member, or None if not in any team.

    None → user is not in any team → normal unrestricted access.
    list → user is a team member → only IDs in the list are accessible.

    resource_type: 'agents' | 'connections' | 'knowledge'
    perm examples: 'use' (agents), 'direct' (connections), 'view' (knowledge)
    """
    from app.config.data import DB_FILE
    from app.storage.teams import TeamStorage

    ts = TeamStorage(DB_FILE)
    teams = ts.list_teams_for_user(username)
    if not teams:
        return None  # No team membership → unrestricted

    allowed: set[str] = set()
    for t in teams:
        member = ts.get_member(t["id"], username)
        if not member:
            continue
        perms = member.get("permissions") or {}
        section = perms.get(resource_type, {})
        default = section.get("default", "deny")
        items = section.get("items", {})

        if default == "allow":
            # All allowed except explicitly denied
            # We can't enumerate all resource IDs here, signal "all permitted" with None
            return None
        else:
            for rid, rperms in items.items():
                if rperms.get(perm, False):
                    allowed.add(rid)

    return list(allowed)


def filter_by_team(
    username: str,
    resource_type: str,
    perm: str,
    items: List[Dict[str, Any]],
    id_field: str = "id",
) -> List[Dict[str, Any]]:
    """Filter a list of resource dicts by team permissions.

    Returns original list unchanged if user is not in any team.
    """
    allowed = get_team_allowed_ids(username, resource_type, perm)
    if allowed is None:
        return items
    allowed_set = set(allowed)
    return [it for it in items if it.get(id_field) in allowed_set]
