from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.services.hub_sync import (
    _get_all_remote_cursor_pages,
    _get_remote_cursor_page,
    _get_remote_json,
    _RemotePage,
)


def test_remote_json_uses_safe_transport_for_the_actual_request():
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"ok": true}'
    response.headers = {}

    with patch(
        "app.services.hub_sync.safe_urlopen", return_value=response
    ) as safe_open:
        result = asyncio.run(
            _get_remote_json(
                "https://hub.example.com", "/api/agents", {"Cookie": "token"}
            )
        )

    assert result == {"ok": True}
    request = safe_open.call_args.args[0]
    assert request.full_url == "https://hub.example.com/api/agents"
    assert safe_open.call_args.kwargs == {"timeout": 30}


def test_remote_page_reads_v2_body_instead_of_pagination_headers():
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = (
        b'{"items":[{"id":"a1"}],"page":'
        b'{"has_more":true,"next_cursor":"signed"}}'
    )
    response.headers = {}
    with patch("app.services.hub_sync.safe_urlopen", return_value=response):
        page = asyncio.run(
            _get_remote_cursor_page(
                "https://hub.example.com", "/api/v2/agents", {}
            )
        )

    assert page.payload == [{"id": "a1"}]
    assert page.has_more is True
    assert page.next_cursor == "signed"


def test_remote_cursor_page_rejects_a_legacy_list():
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'[{"id":"legacy"}]'
    response.headers = {"X-Has-More": "false"}
    with patch("app.services.hub_sync.safe_urlopen", return_value=response):
        with pytest.raises(ValueError, match="envelope cursor v2"):
            asyncio.run(
                _get_remote_cursor_page(
                    "https://hub.example.com", "/api/v2/agents", {}
                )
            )


def test_cursor_page_reader_preserves_filters_and_forwards_opaque_cursor():
    first = [{"id": str(index)} for index in range(100)]
    cursor = "opaque+/= cursor"
    with patch(
        "app.services.hub_sync._get_remote_cursor_page",
        side_effect=[
            _RemotePage(first, has_more=True, next_cursor=cursor),
            _RemotePage([{"id": "last"}], has_more=False, next_cursor=None),
        ],
    ) as get_page:
        result = asyncio.run(
            _get_all_remote_cursor_pages(
                "https://hub.example.com",
                "/api/agents?scope=private",
                {"Cookie": "token"},
            )
        )

    assert len(result) == 101
    assert get_page.call_args_list[0].args[1] == (
        "/api/agents?scope=private&limit=100"
    )
    assert get_page.call_args_list[1].args[1] == (
        "/api/agents?scope=private&limit=100&cursor=opaque%2B%2F%3D+cursor"
    )


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ([_RemotePage([], True, None)], "sin cursor"),
        (
            [
                _RemotePage([], True, "same"),
                _RemotePage([], True, "same"),
            ],
            "repitió el cursor",
        ),
    ],
)
def test_cursor_page_reader_rejects_broken_remote_contract(pages, message):
    with patch("app.services.hub_sync._get_remote_cursor_page", side_effect=pages):
        with pytest.raises(ValueError, match=message):
            asyncio.run(
                _get_all_remote_cursor_pages(
                    "https://hub.example.com", "/api/skills", {}
                )
            )
