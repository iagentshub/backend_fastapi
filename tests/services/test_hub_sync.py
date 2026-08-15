from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from app.services.hub_sync import _get_all_remote_pages, _get_remote_json


def test_remote_json_uses_safe_transport_for_the_actual_request():
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"ok": true}'

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


def test_remote_page_reader_preserves_filters_and_advances_offset():
    first = [{"id": str(index)} for index in range(100)]
    second = [{"id": "last"}]
    with patch(
        "app.services.hub_sync._get_remote_json",
        side_effect=[first, second],
    ) as get_json:
        result = asyncio.run(
            _get_all_remote_pages(
                "https://hub.example.com",
                "/api/agents?scope=private",
                {"Cookie": "token"},
            )
        )

    assert len(result) == 101
    assert get_json.call_args_list[0].args[1] == (
        "/api/agents?scope=private&limit=100&offset=0"
    )
    assert get_json.call_args_list[1].args[1].endswith("offset=100")


def test_remote_page_reader_stops_if_legacy_server_ignores_offset():
    page = [{"id": str(index)} for index in range(100)]
    with patch(
        "app.services.hub_sync._get_remote_json",
        side_effect=[page, page],
    ) as get_json:
        result = asyncio.run(
            _get_all_remote_pages("https://hub.example.com", "/api/skills", {})
        )

    assert result == page
    assert get_json.call_count == 2
