"""Tests de ConnectionStorage."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.storage.storage import ConnectionStorage


@pytest.fixture()
def storage(tmp_path):
    return ConnectionStorage(tmp_path / "connections" / "connections.json")


def test_list_empty(storage):
    assert storage.list() == []


def test_save_and_list(storage):
    conn = storage.save({"type": "openai", "label": "Mi OpenAI", "api_key": "sk-test"})
    assert "id" in conn
    items = storage.list()
    assert len(items) == 1
    assert items[0]["type"] == "openai"


def test_get_by_id(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-test"})
    found = storage.get(conn["id"])
    assert found is not None
    assert found["id"] == conn["id"]


def test_get_nonexistent(storage):
    assert storage.get("ghost-id") is None


def test_save_updates_existing(storage):
    conn = storage.save({"type": "openai", "label": "V1", "api_key": "sk-test"})
    conn["label"] = "V2"
    updated = storage.save(conn)
    assert updated["label"] == "V2"
    assert len(storage.list()) == 1


def test_save_preserves_created_at(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-test"})
    original_created = conn["created_at"]
    conn["label"] = "Changed"
    updated = storage.save(conn)
    assert updated["created_at"] == original_created


def test_delete_existing(storage):
    conn = storage.save({"type": "openai", "api_key": "sk-test"})
    assert storage.delete(conn["id"]) is True
    assert storage.list() == []


def test_delete_nonexistent(storage):
    assert storage.delete("ghost-id") is False
