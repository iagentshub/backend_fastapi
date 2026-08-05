"""Los storages SQL usan exclusivamente la configuración global de open_db()."""

from __future__ import annotations

import inspect

import pytest

from app.storage.accounts import AccountStorage
from app.storage.billing import BillingStorage
from app.storage.chat import ChatStorage
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import ConnectionStorage


@pytest.mark.parametrize(
    "storage_cls",
    (
        AccountStorage,
        BillingStorage,
        ChatStorage,
        ConnectionStorage,
        GroupShareStorage,
        GroupStorage,
        KnowledgeStorage,
    ),
)
def test_sql_storage_constructor_has_no_fake_db_path(storage_cls):
    assert list(inspect.signature(storage_cls).parameters) == []
