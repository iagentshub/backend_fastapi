"""Security and readiness rules shared by Tool consumption boundaries."""

import pytest

from app.errors import APIError
from app.services.tool_policy import (
    assert_tool_consumable,
    assert_tool_distributable,
)


def test_owner_can_use_reviewed_tool_but_cannot_distribute_it():
    tool = {
        "id": "tool-1",
        "owner_id": "group-1",
        "language": "python",
        "content": "print(1)",
        "labels": ["review"],
        "is_active": True,
    }

    assert_tool_consumable(tool, user_id="user-1", group_id="group-1", is_admin=False)
    with pytest.raises(APIError) as blocked:
        assert_tool_distributable(tool)
    assert blocked.value.status_code == 403


def test_not_ready_tool_is_never_consumable_even_by_owner():
    tool = {
        "id": "tool-1",
        "owner_id": "group-1",
        "language": "cpp",
        "labels": [],
        "is_active": True,
    }

    with pytest.raises(APIError) as blocked:
        assert_tool_consumable(
            tool, user_id="user-1", group_id="group-1", is_admin=False
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["field"] == "implementation"


def test_inactive_tool_is_never_consumable():
    tool = {
        "id": "tool-1",
        "owner_id": "group-1",
        "language": "python",
        "content": "print(1)",
        "labels": [],
        "is_active": False,
    }

    with pytest.raises(APIError) as blocked:
        assert_tool_consumable(
            tool, user_id="user-1", group_id="group-1", is_admin=False
        )
    assert blocked.value.detail["code"] == "resource_inactive"


@pytest.mark.parametrize(
    ("user_id", "group_id", "is_admin"),
    [
        ("owner", "group-1", False),
        ("admin", "admin-group", True),
        ("other", "other-group", False),
    ],
)
def test_quarantined_tool_is_never_consumable(
    user_id: str, group_id: str, is_admin: bool
):
    tool = {
        "id": "tool-1",
        "owner_id": "group-1",
        "language": "python",
        "content": "print(1)",
        "labels": ["quarantine"],
        "is_active": True,
    }

    with pytest.raises(APIError) as blocked:
        assert_tool_consumable(
            tool,
            user_id=user_id,
            group_id=group_id,
            is_admin=is_admin,
        )

    assert blocked.value.status_code == 403
    assert blocked.value.detail["labels"] == ["quarantine"]


def test_admin_cannot_consume_a_reviewed_tool_owned_by_someone_else():
    tool = {
        "id": "tool-1",
        "owner_id": "group-1",
        "language": "python",
        "content": "print(1)",
        "labels": ["review"],
        "is_active": True,
    }

    with pytest.raises(APIError) as blocked:
        assert_tool_consumable(
            tool,
            user_id="admin",
            group_id="admin-group",
            is_admin=True,
        )

    assert blocked.value.status_code == 403
