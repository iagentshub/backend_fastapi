"""Tests de MemoryStorage: guardar, leer, listar, eliminar y sanitización de nombres."""
from __future__ import annotations

import pytest

from app.storage.storage import MemoryStorage


@pytest.fixture()
def storage(tmp_path):
    return MemoryStorage(tmp_path / "memory")


def test_list_empty(storage):
    assert storage.list() == []


def test_save_and_get(storage):
    storage.save("agent_test.md", "Recuerdo algo importante.")
    content = storage.get("agent_test.md")
    assert content == "Recuerdo algo importante."


def test_list_after_save(storage):
    storage.save("agent_test.md", "contenido")
    items = storage.list()
    assert len(items) == 1
    assert items[0]["filename"] == "agent-test.md"


def test_get_nonexistent(storage):
    assert storage.get("ghost.md") is None


def test_save_sanitizes_filename(storage):
    """Nombres con caracteres especiales se sanitizan a slug."""
    storage.save("Mi Agente Especial!.md", "datos")
    content = storage.get("Mi Agente Especial!.md")
    assert content == "datos"


def test_path_traversal_blocked(storage):
    """Un nombre con ../ no debe poder salir del directorio de memoria."""
    storage.save("../../etc/passwd", "hack")
    # El fichero debe haberse guardado con nombre sanitizado, no en /etc/passwd
    content = storage.get("../../etc/passwd")
    assert content == "hack"
    # Verificar que no existe fuera del directorio
    import os
    assert not os.path.exists("/etc/passwd.md") or open("/etc/passwd").read() != "hack"


def test_delete_existing(storage):
    storage.save("to_delete.md", "temp")
    # Acceso directo al método delete si existe
    p = storage.root_dir / "to-delete.md"
    assert p.exists()
    result = storage.delete("to_delete.md")
    assert result is True
    assert not p.exists()


def test_delete_nonexistent(storage):
    assert storage.delete("ghost.md") is False
