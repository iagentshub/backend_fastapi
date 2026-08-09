from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from app.services.hub_sync import _get_remote_json


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
