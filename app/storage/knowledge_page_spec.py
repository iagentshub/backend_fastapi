"""Construcción compartida de consultas paginadas de Knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.resource_visibility import VisibilityFilter


@dataclass(frozen=True, slots=True)
class KnowledgePageQuery:
    where: str
    params: tuple[Any, ...]
    columns: str


def knowledge_page_query(
    *,
    user: str,
    owner_id: str,
    type: str | None,
    permission_filter: VisibilityFilter | None,
    requested_group_id: str | None,
    catalog_filter: VisibilityFilter | None,
) -> KnowledgePageQuery:
    if requested_group_id is not None:
        direct_sql = (
            "EXISTS (SELECT 1 FROM resource_group_shares direct_share "
            "WHERE direct_share.resource_type='knowledge' "
            "AND direct_share.resource_id=k.id AND direct_share.group_id=?)"
        )
        pack_sql = (
            "(k.pack_id IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM resource_group_shares pack_share "
            "WHERE pack_share.resource_type='knowledge_pack' "
            "AND pack_share.resource_id=k.pack_id AND pack_share.group_id=?))"
        )
        direct_params: tuple[Any, ...] = (requested_group_id,)
        pack_params: tuple[Any, ...] = (requested_group_id,)
        owner_active = (
            "NOT EXISTS (SELECT 1 FROM groups inactive_owner "
            "WHERE inactive_owner.id=k.owner_id AND inactive_owner.is_active=0)"
        )
        visibility_sql = f"((({direct_sql}) OR ({pack_sql})) AND {owner_active})"
        visibility_params = (*direct_params, *pack_params)
    else:
        membership = (
            "JOIN group_members visible_member "
            "ON visible_member.group_id=visible_share.group_id "
            "JOIN groups visible_group ON visible_group.id=visible_share.group_id "
        )
        direct_sql = (
            "EXISTS (SELECT 1 FROM resource_group_shares visible_share "
            f"{membership}WHERE visible_share.resource_type='knowledge' "
            "AND visible_share.resource_id=k.id "
            "AND visible_member.username=? AND visible_group.is_active=1)"
        )
        pack_sql = (
            "(k.pack_id IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM resource_group_shares visible_share "
            f"{membership}WHERE visible_share.resource_type='knowledge_pack' "
            "AND visible_share.resource_id=k.pack_id "
            "AND visible_member.username=? AND visible_group.is_active=1))"
        )
        direct_params = (user,)
        pack_params = (user,)
        owner_active = (
            "NOT EXISTS (SELECT 1 FROM groups inactive_owner "
            "WHERE inactive_owner.id=k.owner_id AND inactive_owner.is_active=0)"
        )
        visibility_sql = (
            f"(k.owner_id=? OR ((({direct_sql}) OR ({pack_sql})) "
            f"AND {owner_active}))"
        )
        visibility_params = (owner_id, *direct_params, *pack_params)

    clauses = [visibility_sql]
    params = list(visibility_params)
    if type:
        clauses.append("k.type=?")
        params.append(type)
    if permission_filter is not None:
        clauses.append(f"(({pack_sql}) OR ({permission_filter.sql}))")
        params.extend(pack_params)
        params.extend(permission_filter.params)
    if catalog_filter is not None:
        clauses.append(catalog_filter.sql)
        params.extend(catalog_filter.params)
    return KnowledgePageQuery(
        where=" AND ".join(f"({clause})" for clause in clauses),
        params=tuple(params),
        columns=(
            "k.id,k.owner_id,k.type,k.title,k.source,k.char_count,"
            "k.source_char_count,k.content_truncated,k.truncation_reason,k.mime_type,"
            "k.size_bytes,k.checksum,k.labels,k.is_active,k.deactivated_at,"
            "k.created_at,k.updated_at,k.pack_id,k.pack_relative_path,k.pack_kind"
        ),
    )
