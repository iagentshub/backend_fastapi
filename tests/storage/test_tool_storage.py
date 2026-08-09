"""Tests de ToolStorage: CRUD, validación de language, contenido dual
texto/binario, preservación del binario en ediciones de solo texto."""

from __future__ import annotations

import asyncio
import re

import pytest

from app.storage._storage_helpers import _PUBLIC_OWNER
from app.storage.tool_storage import ToolStorage


@pytest.fixture()
def storage(tmp_path, patch_data_dir):  # noqa: ARG001
    return ToolStorage()


_TOOL = {
    "name": "Mi Tool",
    "description": "Una tool de prueba.",
    "content": "print('hola')",
    "icon": "🛠️",
    "language": "python",
}


def test_list_empty(storage):
    assert asyncio.run(storage.list()) == []


def test_save_private_tool(storage):
    tl = asyncio.run(storage.save("private", _TOOL))
    assert tl["name"] == "Mi Tool"
    assert "id" in tl
    assert tl["scope"] == "private"
    assert tl["language"] == "python"
    assert tl["content"] == "print('hola')"


def test_save_public_requires_owner(storage):
    with pytest.raises(ValueError, match="sistema"):
        asyncio.run(storage.save("public", _TOOL))


def test_save_owned_public_tool(storage):
    tool = asyncio.run(storage.save("public", _TOOL, owner_id="user-id"))
    assert tool["scope"] == "public"
    assert tool["owner_id"] == "user-id"
    assert tool["labels"] == ["public", "community"]


# ── language ─────────────────────────────────────────────────────────────────


def test_language_empty_rejected(storage):
    with pytest.raises(ValueError, match="invalid tool language"):
        asyncio.run(storage.save("private", {**_TOOL, "language": ""}))


def test_language_invalid_rejected(storage):
    with pytest.raises(ValueError, match="invalid tool language"):
        asyncio.run(storage.save("private", {**_TOOL, "language": "ruby"}))


def test_shell_language_keeps_content(storage):
    tl = asyncio.run(
        storage.save("private", {**_TOOL, "language": "shell", "content": "echo hi"})
    )
    assert tl["language"] == "shell"
    assert tl["content"] == "echo hi"


def test_cpp_language_forces_empty_content(storage):
    tl = asyncio.run(
        storage.save(
            "private", {**_TOOL, "language": "cpp", "content": "int main(){}"}
        )
    )
    assert tl["language"] == "cpp"
    assert tl["content"] == ""


# ── CRUD básico ─────────────────────────────────────────────────────────────


def test_get_private_tool(storage):
    tl = asyncio.run(storage.save("private", _TOOL))
    found = asyncio.run(storage.get("private", tl["id"]))
    assert found is not None
    assert found["name"] == "Mi Tool"
    assert "content" in found


def test_save_generates_unique_alphanumeric_id(storage):
    """El id no debe derivar del nombre: dos tools con el mismo nombre (de
    dueños distintos) deben recibir ids distintos."""
    t1 = asyncio.run(storage.save("private", _TOOL, owner_id="alice"))
    t2 = asyncio.run(storage.save("private", _TOOL, owner_id="bob"))
    assert t1["id"] != t2["id"]
    assert t1["id"] != "mi-tool"
    assert re.fullmatch(r"[0-9a-f]{12}", t1["id"])


def test_get_nonexistent_tool(storage):
    assert asyncio.run(storage.get("private", "ghost-tool")) is None


def test_list_shows_private_tools(storage):
    asyncio.run(storage.save("private", _TOOL))
    items = asyncio.run(storage.list("private"))
    assert len(items) == 1
    assert items[0]["name"] == "Mi Tool"


def test_delete_private_tool(storage):
    tl = asyncio.run(storage.save("private", _TOOL))
    assert asyncio.run(storage.delete("private", tl["id"])) is True
    assert asyncio.run(storage.get("private", tl["id"])) is None


def test_delete_system_public_raises(storage):
    with pytest.raises(ValueError, match="sistema"):
        asyncio.run(storage.delete("public", "some-tool"))


def test_delete_owned_public_tool(storage):
    tool = asyncio.run(storage.save("public", _TOOL, owner_id="user-id"))
    assert (
        asyncio.run(storage.delete("public", tool["id"], owner_id="user-id")) is True
    )


def test_delete_nonexistent_tool(storage):
    assert asyncio.run(storage.delete("private", "ghost-tool")) is False


# ── owner_id + get_any ─────────────────────────────────────────────────────────


def test_save_sets_owner_id(storage):
    tl = asyncio.run(storage.save("private", _TOOL, owner_id="alice"))
    assert tl.get("owner_id") == "alice"


def test_owner_id_persisted(storage):
    tl = asyncio.run(storage.save("private", _TOOL, owner_id="alice"))
    found = asyncio.run(storage.get("private", tl["id"]))
    assert found is not None
    assert found.get("owner_id") == "alice"


def test_save_without_owner_id_has_no_owner(storage):
    """Sin owner_id explícito, la tool se asigna a 'admin' por defecto."""
    tl = asyncio.run(storage.save("private", _TOOL))
    assert tl.get("owner_id") == "admin"


def test_get_any_finds_private_tool(storage):
    tl = asyncio.run(storage.save("private", _TOOL))
    found = asyncio.run(storage.get_any(tl["id"]))
    assert found is not None
    assert found["name"] == _TOOL["name"]


def test_get_any_finds_public_tool(storage):
    """Inserta una tool pública directamente en la DB y verifica get_any."""
    from app.storage.db import open_db

    async def _insert():
        async with open_db() as conn:
            await storage._upsert(
                conn,
                "pub-tool",
                _PUBLIC_OWNER,
                "public",
                {
                    "id": "pub-tool",
                    "name": "Tool Publica",
                    "content": "echo hi",
                    "language": "shell",
                },
            )
            await conn.commit()

    asyncio.run(_insert())
    found = asyncio.run(storage.get_any("pub-tool"))
    assert found is not None
    assert found["name"] == "Tool Publica"


def test_get_any_returns_none_for_missing(storage):
    assert asyncio.run(storage.get_any("ghost-tool-xyz")) is None


def test_delete_with_owner_id_does_not_touch_other_owner_same_id(storage):
    """Borrar la propia tool no debe borrar la de otro dueño con el mismo id."""
    shared_id = "legacy-shared-id"
    asyncio.run(
        storage.save("private", {**_TOOL, "id": shared_id}, owner_id="alice")
    )
    asyncio.run(storage.save("private", {**_TOOL, "id": shared_id}, owner_id="bob"))

    deleted = asyncio.run(storage.delete("private", shared_id, owner_id="alice"))
    assert deleted is True

    bob_tool = asyncio.run(storage.get("private", shared_id))
    assert bob_tool is not None
    assert bob_tool["owner_id"] == "bob"


def test_delete_with_owner_id_rejects_non_owner(storage):
    tl = asyncio.run(storage.save("private", _TOOL, owner_id="alice"))
    deleted = asyncio.run(storage.delete("private", tl["id"], owner_id="bob"))
    assert deleted is False
    assert asyncio.run(storage.get("private", tl["id"])) is not None


# ── content/binary_b64 exposure ─────────────────────────────────────────────


def test_list_excludes_content_and_binary(storage):
    tl = asyncio.run(
        storage.save(
            "private", {**_TOOL, "language": "cpp", "content": ""}, owner_id="bob"
        )
    )
    asyncio.run(storage.save_binary(tl["id"], "bob", "QUJD", "bin1", 3))
    items = asyncio.run(storage.list("private"))
    assert len(items) == 1
    assert "content" not in items[0]
    assert "binary_b64" not in items[0]
    # Los metadatos ligeros del binario sí se exponen en list().
    assert items[0]["binary_filename"] == "bin1"
    assert items[0]["binary_size"] == 3


def test_get_includes_content_and_binary(storage):
    tl = asyncio.run(
        storage.save(
            "private", {**_TOOL, "language": "cpp", "content": ""}, owner_id="alice"
        )
    )
    asyncio.run(storage.save_binary(tl["id"], "alice", "QUJD", "prog", 3))
    found = asyncio.run(storage.get("private", tl["id"]))
    assert found is not None
    assert "content" in found
    assert found["binary_b64"] == "QUJD"


# ── binario ──────────────────────────────────────────────────────────────────


def test_save_binary_and_get_binary(storage):
    tl = asyncio.run(
        storage.save(
            "private", {**_TOOL, "language": "cpp", "content": ""}, owner_id="alice"
        )
    )
    ok = asyncio.run(storage.save_binary(tl["id"], "alice", "QUJD", "prog", 3))
    assert ok is True
    binary = asyncio.run(storage.get_binary("private", tl["id"]))
    assert binary is not None
    assert binary["binary_b64"] == "QUJD"
    assert binary["binary_filename"] == "prog"
    assert binary["binary_size"] == 3


def test_save_binary_nonexistent_tool_returns_false(storage):
    ok = asyncio.run(storage.save_binary("ghost-id", "alice", "QUJD", "prog", 3))
    assert ok is False


def test_get_binary_returns_none_without_upload(storage):
    tl = asyncio.run(
        storage.save(
            "private", {**_TOOL, "language": "cpp", "content": ""}, owner_id="alice"
        )
    )
    assert asyncio.run(storage.get_binary("private", tl["id"])) is None


def test_get_binary_returns_none_for_missing_tool(storage):
    assert asyncio.run(storage.get_binary("private", "ghost-tool")) is None


def test_binary_preserved_after_editing_only_name(storage):
    """Regresión directa del footgun de upsert descrito en el plan: editar
    solo texto (nombre) no debe resetear a NULL el binario ya subido."""
    tl = asyncio.run(
        storage.save(
            "private", {**_TOOL, "language": "cpp", "content": ""}, owner_id="alice"
        )
    )
    asyncio.run(storage.save_binary(tl["id"], "alice", "QUJD", "prog", 3))

    # Payload realista de una edición de solo texto: sin binary_b64 (nunca se
    # expone en las respuestas de list()/get() al cliente).
    edit_payload = {
        "id": tl["id"],
        "name": "Nombre Editado",
        "description": tl["description"],
        "language": "cpp",
    }
    updated = asyncio.run(storage.save("private", edit_payload, owner_id="alice"))
    assert updated["name"] == "Nombre Editado"

    binary = asyncio.run(storage.get_binary("private", tl["id"]))
    assert binary is not None
    assert binary["binary_b64"] == "QUJD"
    assert binary["binary_filename"] == "prog"
    assert binary["binary_size"] == 3
