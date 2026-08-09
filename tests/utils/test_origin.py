from __future__ import annotations

import pytest

from app.errors import APIError
from app.utils.origin import (
    assert_resource_writable,
    compute_origin_type,
    resource_labels,
)


def test_property_type_supports_owner_link_and_fork() -> None:
    assert compute_origin_type({}) == "owner"
    assert compute_origin_type({"_shared": True}) == "linked"
    assert compute_origin_type({"labels": ["private", "linked"]}) == "linked"
    assert compute_origin_type({"labels": '["private", "fork"]'}) == "fork"


def test_fork_takes_priority_and_is_writable() -> None:
    resource = {"_shared": True, "labels": ["private", "fork"]}
    assert compute_origin_type(resource) == "fork"
    assert_resource_writable(resource, "agent")


def test_link_is_never_writable() -> None:
    with pytest.raises(APIError) as exc_info:
        assert_resource_writable({"labels": ["private", "linked"]}, "skill")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "linked_resource_read_only"


def test_invalid_serialized_labels_are_ignored() -> None:
    assert resource_labels({"labels": "not-json"}) == set()
