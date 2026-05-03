"""Tests de SkillStorage: CRUD private, lectura public, fallback _slug."""
from __future__ import annotations

import pytest

from app.storage.storage import SkillStorage


@pytest.fixture()
def storage(tmp_path):
    return SkillStorage(tmp_path / "skills")


_SKILL = {
    "name": "Mi Skill",
    "description": "Una skill de prueba.",
    "content": "Haz algo útil.",
    "icon": "🔧",
}


def test_list_empty(storage):
    assert storage.list() == []


def test_save_private_skill(storage):
    sk = storage.save("private", _SKILL)
    assert sk["name"] == "Mi Skill"
    assert "id" in sk
    assert sk["scope"] == "private"


def test_save_public_raises(storage):
    with pytest.raises(ValueError, match="solo lectura"):
        storage.save("public", _SKILL)


def test_get_private_skill(storage):
    sk = storage.save("private", _SKILL)
    found = storage.get("private", sk["id"])
    assert found is not None
    assert found["name"] == "Mi Skill"
    assert "content" in found


def test_get_skill_slug_fallback(storage):
    """get() debe encontrar la skill por slug aunque se pase el nombre original."""
    sk = storage.save("private", _SKILL)
    # El id generado es el slug, pasar el nombre con mayúsculas debe funcionar
    found = storage.get("private", "Mi Skill")
    assert found is not None
    assert found["id"] == sk["id"]


def test_get_nonexistent_skill(storage):
    assert storage.get("private", "ghost-skill") is None


def test_list_shows_private_skills(storage):
    storage.save("private", _SKILL)
    items = storage.list("private")
    assert len(items) == 1
    assert items[0]["name"] == "Mi Skill"


def test_delete_private_skill(storage):
    sk = storage.save("private", _SKILL)
    assert storage.delete("private", sk["id"]) is True
    assert storage.get("private", sk["id"]) is None


def test_delete_public_raises(storage):
    with pytest.raises(ValueError, match="solo lectura"):
        storage.delete("public", "some-skill")


def test_delete_nonexistent_skill(storage):
    assert storage.delete("private", "ghost-skill") is False


def test_get_skill_by_frontmatter_id(storage, tmp_path):
    """get() debe encontrar una skill cuyo directorio tiene nombre distinto al id del frontmatter."""
    # Simula la situación real: carpeta 'blogwatcher' con id: MONITOR_BLOGS
    skill_dir = storage.root_dir / "public" / "blogwatcher"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nid: MONITOR_BLOGS\nname: Monitor de Blogs\ndescription: test\nicon: 📰\ncategory: productivity\n---\n\nContenido de la skill.\n",
        encoding="utf-8",
    )
    found = storage.get("public", "MONITOR_BLOGS")
    assert found is not None
    assert found["name"] == "Monitor de Blogs"
    assert found["content"] == "Contenido de la skill."


def test_get_skill_frontmatter_id_case_insensitive(storage):
    """El fallback por id es case-insensitive: 'monitor_blogs' == 'MONITOR_BLOGS'."""
    skill_dir = storage.root_dir / "public" / "blogwatcher"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nid: MONITOR_BLOGS\nname: Monitor de Blogs\ndescription: test\nicon: 📰\ncategory: productivity\n---\n\nContenido.\n",
        encoding="utf-8",
    )
    assert storage.get("public", "monitor_blogs") is not None
