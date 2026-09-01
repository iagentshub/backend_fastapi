"""Los IDs entregados al FTS respetan ownership, shares y estado."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.knowledge_access import resolve_accessible_knowledge_ids


async def test_resuelve_solo_knowledge_y_packs_accesibles_activos():
    knowledge = MagicMock()
    knowledge.get_metadata = AsyncMock(
        side_effect=[
            {"id": "own", "owner_id": "alice", "labels": ["private"]},
            {"id": "public", "owner_id": "bob", "labels": ["public"]},
            {"id": "hidden", "owner_id": "bob", "labels": ["private"]},
            {"id": "off", "owner_id": "alice", "is_active": False},
        ]
    )
    packs = MagicMock()
    packs.get = AsyncMock(
        side_effect=[
            {
                "id": "shared-pack",
                "owner_id": "bob",
                "labels": ["private"],
                "items": [{"id": "pack-doc"}],
            },
            {
                "id": "hidden-pack",
                "owner_id": "charlie",
                "labels": ["private"],
                "items": [{"id": "secret-pack-doc"}],
            },
        ]
    )
    shares = MagicMock()
    shares.is_accessible = AsyncMock(
        side_effect=lambda _groups, **kwargs: (
            kwargs["resource_id"] in {"own", "shared-pack"}
        )
    )

    resolved = await resolve_accessible_knowledge_ids(
        knowledge_ids=["own", "public", "hidden", "off"],
        pack_ids=["shared-pack", "hidden-pack"],
        requester="alice",
        requester_group="team-a",
        is_admin=False,
        knowledge=knowledge,
        packs=packs,
        shares=shares,
        groups=MagicMock(),
    )

    assert resolved == ["own", "public", "pack-doc"]
    assert "hidden" not in resolved
    assert "secret-pack-doc" not in resolved


async def test_un_fichero_directo_hereda_acceso_del_pack_compartido():
    knowledge = MagicMock()
    knowledge.get_metadata = AsyncMock(
        return_value={
            "id": "pack-member",
            "owner_id": "bob",
            "labels": ["private"],
            "pack_id": "shared-parent",
        }
    )
    packs = MagicMock()
    packs.get = AsyncMock(
        return_value={
            "id": "shared-parent",
            "owner_id": "bob",
            "labels": ["private"],
            "is_active": True,
        }
    )
    shares = MagicMock()
    shares.is_accessible = AsyncMock(
        side_effect=lambda _groups, **kwargs: (
            kwargs["resource_type"] == "knowledge_pack"
        )
    )

    resolved = await resolve_accessible_knowledge_ids(
        knowledge_ids=["pack-member"],
        pack_ids=[],
        requester="alice",
        requester_group="alice",
        is_admin=False,
        knowledge=knowledge,
        packs=packs,
        shares=shares,
        groups=MagicMock(),
    )

    assert resolved == ["pack-member"]
    packs.get.assert_awaited_once_with("shared-parent", include_items=False)
