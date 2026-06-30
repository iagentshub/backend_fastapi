"""Tests unitarios del helper _group_filter.get_group_shared_ids()."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.api.routes._group_filter import get_group_shared_ids


def _mock_gs(return_value):
    """Construye un mock de GroupStorage con get_user_shared_resource_ids síncrono."""
    instance = MagicMock()
    instance.get_user_shared_resource_ids.return_value = return_value
    return instance


def test_returns_none_when_no_shared_resources():
    """Lista vacía de recursos compartidos debe producir None."""
    # GroupStorage se importa de forma lazy dentro de la función, hay que parchear
    # el símbolo en su módulo origen (app.storage.groups).
    with patch("app.storage.groups.GroupStorage", return_value=_mock_gs([])):
        result = get_group_shared_ids("alice", "agent", "ws-1")
    assert result is None


def test_returns_list_when_shared_resources_exist():
    """Lista no vacía de IDs debe devolverse tal cual."""
    shared = ["agent-aaa", "agent-bbb"]
    with patch("app.storage.groups.GroupStorage", return_value=_mock_gs(shared)):
        result = get_group_shared_ids("bob", "agent", "ws-2")
    assert result == shared


def test_forwards_correct_parameters_to_storage():
    """get_group_shared_ids debe pasar los parámetros exactos a GroupStorage."""
    mock_instance = _mock_gs(["id-x"])
    with patch("app.storage.groups.GroupStorage", return_value=mock_instance):
        get_group_shared_ids("charlie", "skill", "ws-42")
    mock_instance.get_user_shared_resource_ids.assert_called_once_with(
        "charlie", "skill", "ws-42"
    )


def test_uses_db_file_to_init_group_storage():
    """GroupStorage debe instanciarse con DB_FILE del config."""
    mock_instance = _mock_gs([])
    with patch("app.storage.groups.GroupStorage", return_value=mock_instance) as MockGS:
        with patch("app.config.data.DB_FILE", "/fake/path/hub.db"):
            get_group_shared_ids("user", "connection", "ws-9")
    MockGS.assert_called_once()


def test_single_null_list_returns_none():
    """Lista vacía es el único caso falsy relevante — debe devolver None."""
    with patch("app.storage.groups.GroupStorage", return_value=_mock_gs([])):
        assert get_group_shared_ids("x", "agent", "ws") is None
