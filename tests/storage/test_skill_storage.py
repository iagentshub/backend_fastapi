"""Tests de SkillStorage: CRUD private, lectura public, fallback _slug."""

from __future__ import annotations

import asyncio

import pytest

from app.storage.storage import _PUBLIC_OWNER, SkillStorage


@pytest.fixture()
def storage(tmp_path, patch_data_dir):  # noqa: ARG001
    return SkillStorage(tmp_path / "skills")


_SKILL = {
    "name": "Mi Skill",
    "description": "Una skill de prueba.",
    "content": "Haz algo útil.",
    "icon": "🔧",
}


def test_list_empty(storage):
    assert asyncio.run(storage.list()) == []


def test_save_private_skill(storage):
    sk = asyncio.run(storage.save("private", _SKILL))
    assert sk["name"] == "Mi Skill"
    assert "id" in sk
    assert sk["scope"] == "private"


def test_save_public_raises(storage):
    with pytest.raises(ValueError, match="solo lectura"):
        asyncio.run(storage.save("public", _SKILL))


def test_get_private_skill(storage):
    sk = asyncio.run(storage.save("private", _SKILL))
    found = asyncio.run(storage.get("private", sk["id"]))
    assert found is not None
    assert found["name"] == "Mi Skill"
    assert "content" in found


def test_get_skill_slug_fallback(storage):
    """get() debe encontrar la skill por slug aunque se pase el nombre original."""
    sk = asyncio.run(storage.save("private", _SKILL))
    found = asyncio.run(storage.get("private", "Mi Skill"))
    assert found is not None
    assert found["id"] == sk["id"]


def test_get_nonexistent_skill(storage):
    assert asyncio.run(storage.get("private", "ghost-skill")) is None


def test_list_shows_private_skills(storage):
    asyncio.run(storage.save("private", _SKILL))
    items = asyncio.run(storage.list("private"))
    assert len(items) == 1
    assert items[0]["name"] == "Mi Skill"


def test_delete_private_skill(storage):
    sk = asyncio.run(storage.save("private", _SKILL))
    assert asyncio.run(storage.delete("private", sk["id"])) is True
    assert asyncio.run(storage.get("private", sk["id"])) is None


def test_delete_public_raises(storage):
    with pytest.raises(ValueError, match="solo lectura"):
        asyncio.run(storage.delete("public", "some-skill"))


def test_delete_nonexistent_skill(storage):
    assert asyncio.run(storage.delete("private", "ghost-skill")) is False


def test_get_skill_by_frontmatter_id(storage):
    """Skill guardada con id personalizado se recupera por ese id."""
    payload = {
        "id": "MONITOR_BLOGS",
        "name": "Monitor de Blogs",
        "description": "test",
        "icon": "📰",
        "category": "productivity",
        "content": "Contenido de la skill.",
    }
    asyncio.run(storage.save("private", payload))
    found = asyncio.run(storage.get("private", "MONITOR_BLOGS"))
    assert found is not None
    assert found["name"] == "Monitor de Blogs"
    assert found["content"] == "Contenido de la skill."


def test_get_skill_frontmatter_id_case_insensitive(storage):
    """El slug del id es lowercase; buscar por slug funciona."""
    payload = {
        "id": "monitor-blogs",
        "name": "Monitor de Blogs",
        "content": "Contenido.",
    }
    asyncio.run(storage.save("private", payload))
    found = asyncio.run(storage.get("private", "monitor-blogs"))
    assert found is not None


# ── owner_id + get_any ─────────────────────────────────────────────────────────


def test_save_sets_owner_id(storage):
    sk = asyncio.run(storage.save("private", _SKILL, owner_id="alice"))
    assert sk.get("owner_id") == "alice"


def test_owner_id_persisted(storage):
    sk = asyncio.run(storage.save("private", _SKILL, owner_id="alice"))
    found = asyncio.run(storage.get("private", sk["id"]))
    assert found is not None
    assert found.get("owner_id") == "alice"


def test_save_without_owner_id_has_no_owner(storage):
    """Sin owner_id explícito, la skill se asigna a 'admin' por defecto."""
    sk = asyncio.run(storage.save("private", _SKILL))
    assert sk.get("owner_id") == "admin"


def test_get_any_finds_private_skill(storage):
    sk = asyncio.run(storage.save("private", _SKILL))
    found = asyncio.run(storage.get_any(sk["id"]))
    assert found is not None
    assert found["name"] == _SKILL["name"]


def test_get_any_finds_public_skill(storage):
    """Inserta una skill pública directamente en la DB y verifica get_any."""
    from app.storage.db import open_db

    async def _insert():
        async with open_db() as conn:
            await storage._upsert(
                conn,
                "pub-skill",
                _PUBLIC_OWNER,
                "public",
                {"id": "pub-skill", "name": "Skill Publica", "content": "Contenido."},
            )
            await conn.commit()

    asyncio.run(_insert())
    found = asyncio.run(storage.get_any("pub-skill"))
    assert found is not None
    assert found["name"] == "Skill Publica"


def test_get_any_returns_none_for_missing(storage):
    assert asyncio.run(storage.get_any("ghost-skill-xyz")) is None
