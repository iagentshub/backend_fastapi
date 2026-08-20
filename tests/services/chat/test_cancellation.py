"""Cancelación del SSE propagada hasta el transporte bloqueante."""

from __future__ import annotations

import threading

from app.services.chat._streaming import ChatStreamState, _stream_tokens


async def test_closing_stream_closes_provider_and_keeps_partial_reply() -> None:
    provider_closed = threading.Event()

    class Response:
        def close(self) -> None:
            provider_closed.set()

    response = Response()

    def provider(on_token, cancellation):
        cancellation.attach(response)
        try:
            on_token("respuesta parcial")
            provider_closed.wait(timeout=1)
            return "respuesta parcial", 12, 4
        finally:
            cancellation.detach(response)

    state = ChatStreamState()
    state.start(tokens_in=10, connection_id="connection-1")
    output: list[tuple[str, int, int]] = []
    stream = _stream_tokens(output, provider, stream_state=state)

    first = await stream.__anext__()
    await stream.aclose()

    assert '"token": "respuesta parcial"' in first
    assert provider_closed.is_set()
    assert state.interrupted is True
    assert state.completed is False
    assert state.reply == "respuesta parcial"
    assert state.tokens_in == 10
    assert state.tokens_out > 0
    assert output == []
