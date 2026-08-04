"""Tests de PromptStorage: CRUD, alias único por owner, unique_alias()."""

from __future__ import annotations

import asyncio
import re

import pytest

from app.storage.storage import _PUBLIC_OWNER, PromptStorage


@pytest.fixture()
def storage(tmp_path, patch_data_dir):  # noqa: ARG001
    return PromptStorage()


_PROMPT = {
    "name": "Mi Prompt",
    "description": "Un prompt de prueba.",
    "content": "Actúa como un experto en X.",
    "icon": "📝",
    "alias": "mi-prompt",
}


def test_list_empty(storage):
    assert asyncio.run(storage.list()) == []


def test_save_private_prompt(storage):
    pr = asyncio.run(storage.save("private", _PROMPT))
    assert pr["name"] == "Mi Prompt"
    assert "id" in pr
    assert pr["scope"] == "private"
    assert pr["alias"] == "mi-prompt"


def test_save_public_requires_owner(storage):
    with pytest.raises(ValueError, match="sistema"):
        asyncio.run(storage.save("public", _PROMPT))


def test_save_owned_public_prompt(storage):
    prompt = asyncio.run(storage.save("public", _PROMPT, owner_id="user-id"))
    assert prompt["scope"] == "public"
    assert prompt["owner_id"] == "user-id"
    assert prompt["labels"] == ["public"]


def test_alias_is_normalized_to_lowercase(storage):
    pr = asyncio.run(storage.save("private", {**_PROMPT, "alias": "MI-Prompt"}))
    assert pr["alias"] == "mi-prompt"


def test_alias_too_short_rejected(storage):
    with pytest.raises(ValueError, match="Alias inválido"):
        asyncio.run(storage.save("private", {**_PROMPT, "alias": "ab"}))


def test_alias_with_spaces_rejected(storage):
    with pytest.raises(ValueError, match="Alias inválido"):
        asyncio.run(storage.save("private", {**_PROMPT, "alias": "mi prompt"}))


def test_alias_with_symbols_rejected(storage):
    with pytest.raises(ValueError, match="Alias inválido"):
        asyncio.run(storage.save("private", {**_PROMPT, "alias": "mi@prompt!"}))


def test_alias_empty_rejected(storage):
    with pytest.raises(ValueError, match="Alias inválido"):
        asyncio.run(storage.save("private", {**_PROMPT, "alias": ""}))


def test_duplicate_alias_same_owner_raises(storage):
    asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    with pytest.raises(ValueError, match="Ya tienes un prompt con ese alias"):
        asyncio.run(
            storage.save(
                "private",
                {**_PROMPT, "name": "Otro nombre"},
                owner_id="alice",
            )
        )


def test_same_alias_different_owners_allowed(storage):
    p1 = asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    p2 = asyncio.run(storage.save("private", _PROMPT, owner_id="bob"))
    assert p1["alias"] == p2["alias"] == "mi-prompt"
    assert p1["id"] != p2["id"]


def test_editing_own_prompt_keeps_same_alias(storage):
    """Guardar de nuevo el mismo prompt (mismo id) con su propio alias no
    debe dispararse a sí mismo como duplicado."""
    pr = asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    updated = asyncio.run(
        storage.save(
            "private",
            {**pr, "description": "Actualizado"},
            owner_id="alice",
        )
    )
    assert updated["id"] == pr["id"]
    assert updated["description"] == "Actualizado"


def test_get_private_prompt(storage):
    pr = asyncio.run(storage.save("private", _PROMPT))
    found = asyncio.run(storage.get("private", pr["id"]))
    assert found is not None
    assert found["name"] == "Mi Prompt"
    assert "content" in found


def test_save_generates_unique_alphanumeric_id(storage):
    """El id no debe derivar del nombre: dos prompts con el mismo nombre
    (de dueños distintos) deben recibir ids distintos."""
    p1 = asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    p2 = asyncio.run(storage.save("private", _PROMPT, owner_id="bob"))
    assert p1["id"] != p2["id"]
    assert p1["id"] != "mi-prompt"
    assert re.fullmatch(r"[0-9a-f]{12}", p1["id"])


def test_get_nonexistent_prompt(storage):
    assert asyncio.run(storage.get("private", "ghost-prompt")) is None


def test_list_shows_private_prompts(storage):
    asyncio.run(storage.save("private", _PROMPT))
    items = asyncio.run(storage.list("private"))
    assert len(items) == 1
    assert items[0]["name"] == "Mi Prompt"


def test_delete_private_prompt(storage):
    pr = asyncio.run(storage.save("private", _PROMPT))
    assert asyncio.run(storage.delete("private", pr["id"])) is True
    assert asyncio.run(storage.get("private", pr["id"])) is None


def test_delete_system_public_raises(storage):
    with pytest.raises(ValueError, match="sistema"):
        asyncio.run(storage.delete("public", "some-prompt"))


def test_delete_owned_public_prompt(storage):
    prompt = asyncio.run(storage.save("public", _PROMPT, owner_id="user-id"))
    assert (
        asyncio.run(storage.delete("public", prompt["id"], owner_id="user-id"))
        is True
    )


def test_delete_nonexistent_prompt(storage):
    assert asyncio.run(storage.delete("private", "ghost-prompt")) is False


# ── owner_id + get_any ─────────────────────────────────────────────────────────


def test_save_sets_owner_id(storage):
    pr = asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    assert pr.get("owner_id") == "alice"


def test_owner_id_persisted(storage):
    pr = asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    found = asyncio.run(storage.get("private", pr["id"]))
    assert found is not None
    assert found.get("owner_id") == "alice"


def test_save_without_owner_id_has_no_owner(storage):
    """Sin owner_id explícito, el prompt se asigna a 'admin' por defecto."""
    pr = asyncio.run(storage.save("private", _PROMPT))
    assert pr.get("owner_id") == "admin"


def test_get_any_finds_private_prompt(storage):
    pr = asyncio.run(storage.save("private", _PROMPT))
    found = asyncio.run(storage.get_any(pr["id"]))
    assert found is not None
    assert found["name"] == _PROMPT["name"]


def test_get_any_finds_public_prompt(storage):
    """Inserta un prompt público directamente en la DB y verifica get_any."""
    from app.storage.db import open_db

    async def _insert():
        async with open_db() as conn:
            await storage._upsert(
                conn,
                "pub-prompt",
                _PUBLIC_OWNER,
                "public",
                {
                    "id": "pub-prompt",
                    "name": "Prompt Publico",
                    "content": "Contenido.",
                    "alias": "prompt-publico",
                },
            )
            await conn.commit()

    asyncio.run(_insert())
    found = asyncio.run(storage.get_any("pub-prompt"))
    assert found is not None
    assert found["name"] == "Prompt Publico"


def test_get_any_returns_none_for_missing(storage):
    assert asyncio.run(storage.get_any("ghost-prompt-xyz")) is None


def test_delete_with_owner_id_does_not_touch_other_owner_same_id(storage):
    """Borrar el propio prompt no debe borrar el de otro dueño con el mismo id."""
    shared_id = "legacy-shared-id"
    asyncio.run(
        storage.save(
            "private",
            {**_PROMPT, "id": shared_id},
            owner_id="alice",
        )
    )
    asyncio.run(
        storage.save(
            "private",
            {**_PROMPT, "id": shared_id},
            owner_id="bob",
        )
    )

    deleted = asyncio.run(storage.delete("private", shared_id, owner_id="alice"))
    assert deleted is True

    bob_prompt = asyncio.run(storage.get("private", shared_id))
    assert bob_prompt is not None
    assert bob_prompt["owner_id"] == "bob"


def test_delete_with_owner_id_rejects_non_owner(storage):
    pr = asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    deleted = asyncio.run(storage.delete("private", pr["id"], owner_id="bob"))
    assert deleted is False
    assert asyncio.run(storage.get("private", pr["id"])) is not None


# ── unique_alias() ───────────────────────────────────────────────────────────


def test_unique_alias_returns_same_when_free(storage):
    result = asyncio.run(storage.unique_alias("alice", "free-alias"))
    assert result == "free-alias"


def test_unique_alias_suffixes_on_collision(storage):
    asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    result = asyncio.run(storage.unique_alias("alice", "mi-prompt"))
    assert result == "mi-prompt-2"


def test_unique_alias_increments_past_multiple_collisions(storage):
    asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    asyncio.run(
        storage.save("private", {**_PROMPT, "alias": "mi-prompt-2"}, owner_id="alice")
    )
    result = asyncio.run(storage.unique_alias("alice", "mi-prompt"))
    assert result == "mi-prompt-3"


def test_unique_alias_scoped_per_owner(storage):
    asyncio.run(storage.save("private", _PROMPT, owner_id="alice"))
    result = asyncio.run(storage.unique_alias("bob", "mi-prompt"))
    assert result == "mi-prompt"
