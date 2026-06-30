"""Tests de MemoryStorage: guardar, leer, listar, eliminar y sanitización de nombres."""

from __future__ import annotations

import asyncio

import pytest

from app.storage.storage import MemoryStorage


@pytest.fixture()
def storage(tmp_path, patch_data_dir):  # noqa: ARG001
    return MemoryStorage(tmp_path / "memory")


def test_list_empty(storage):
    assert asyncio.run(storage.list()) == []


def test_save_and_get(storage):
    asyncio.run(storage.save("agent_test.md", "Recuerdo algo importante."))
    content = asyncio.run(storage.get("agent_test.md"))
    assert content == "Recuerdo algo importante."


def test_list_after_save(storage):
    asyncio.run(storage.save("agent_test.md", "contenido"))
    items = asyncio.run(storage.list())
    assert len(items) == 1
    assert items[0]["filename"] == "agent-test.md"


def test_get_nonexistent(storage):
    assert asyncio.run(storage.get("ghost.md")) is None


def test_save_sanitizes_filename(storage):
    """Nombres con caracteres especiales se sanitizan a slug."""
    asyncio.run(storage.save("Mi Agente Especial!.md", "datos"))
    content = asyncio.run(storage.get("Mi Agente Especial!.md"))
    assert content == "datos"


def test_path_traversal_blocked(storage):
    """Un nombre con ../ se sanitiza y se almacena de forma segura en la DB."""
    asyncio.run(storage.save("../../etc/passwd", "hack"))
    content = asyncio.run(storage.get("../../etc/passwd"))
    assert content == "hack"


def test_delete_existing(storage):
    asyncio.run(storage.save("to_delete.md", "temp"))
    result = asyncio.run(storage.delete("to_delete.md"))
    assert result is True
    assert asyncio.run(storage.get("to_delete.md")) is None


def test_delete_nonexistent(storage):
    assert asyncio.run(storage.delete("ghost.md")) is False
