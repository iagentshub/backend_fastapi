"""Resolución autorizada de Knowledge vinculado a un agente."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage

_knowledge = KnowledgeStorage()
_packs = KnowledgePackStorage()


async def resolve_agent_knowledge_ids(
    agent: Mapping[str, Any],
    requester: str,
    requester_group: str,
    is_admin: bool,
    shares: GroupShareStorage,
    groups: GroupStorage,
) -> list[str]:
    """Resuelve el Knowledge vinculado usando los storages compartidos del servicio."""

    return await resolve_accessible_knowledge_ids(
        knowledge_ids=agent.get("knowledge") or [],
        pack_ids=agent.get("knowledge_packs") or [],
        requester=requester,
        requester_group=requester_group,
        is_admin=is_admin,
        knowledge=_knowledge,
        packs=_packs,
        shares=shares,
        groups=groups,
    )


async def resolve_accessible_knowledge_ids(
    *,
    knowledge_ids: Iterable[str],
    pack_ids: Iterable[str],
    requester: str,
    requester_group: str,
    is_admin: bool,
    knowledge: KnowledgeStorage,
    packs: KnowledgePackStorage,
    shares: GroupShareStorage,
    groups: GroupStorage,
) -> list[str]:
    """Devuelve solo referencias activas que el usuario puede resolver."""

    resolved: list[str] = []
    for raw_id in knowledge_ids:
        knowledge_id = str(raw_id)
        item = await knowledge.get_metadata(knowledge_id)
        if not item or not item.get("is_active", True):
            continue
        accessible = (
            is_admin
            or "public" in (item.get("labels") or [])
            or await shares.is_accessible(
                groups,
                resource_type="knowledge",
                resource_id=knowledge_id,
                owner_id=item.get("owner_id"),
                requester=requester,
                requester_group=requester_group,
            )
        )
        # Un fichero individual conserva el permiso heredado de su Pack, igual
        # que en el catálogo. Sin esta rama, un agente que referencia ese
        # fichero directamente deja de verlo aunque el Pack esté compartido.
        if not accessible and item.get("pack_id"):
            parent = await packs.get(str(item["pack_id"]), include_items=False)
            accessible = bool(
                parent
                and parent.get("is_active", True)
                and (
                    "public" in (parent.get("labels") or [])
                    or await shares.is_accessible(
                        groups,
                        resource_type="knowledge_pack",
                        resource_id=str(item["pack_id"]),
                        owner_id=parent.get("owner_id"),
                        requester=requester,
                        requester_group=requester_group,
                    )
                )
            )
        if accessible:
            resolved.append(knowledge_id)

    for raw_id in pack_ids:
        pack_id = str(raw_id)
        pack = await packs.get(pack_id)
        if not pack or not pack.get("is_active", True):
            continue
        if (
            not is_admin
            and "public" not in (pack.get("labels") or [])
            and not await shares.is_accessible(
                groups,
                resource_type="knowledge_pack",
                resource_id=pack_id,
                owner_id=pack.get("owner_id"),
                requester=requester,
                requester_group=requester_group,
            )
        ):
            continue
        resolved.extend(
            str(member.get("id"))
            for member in pack.get("items") or []
            if member.get("id")
        )
    return list(dict.fromkeys(resolved))
