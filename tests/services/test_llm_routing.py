from __future__ import annotations

import json

import pytest

from app.services.llm_routing import stream_orchestrated_chat


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@pytest.mark.asyncio
async def test_stack_fails_over_before_first_token(monkeypatch):
    async def fake_stream(_agent, connection, _history, _skills, *args, **kwargs):
        if connection["id"] == "first":
            yield _frame({"type": "error", "message": "401"})
        else:
            yield _frame({"type": "token", "token": "ok"})
            yield _frame({"type": "done", "reply": "ok", "tokens": {"in": 2, "out": 1}})

    monkeypatch.setattr("app.services.llm_routing.stream_chat", fake_stream)
    frames = [
        frame
        async for frame in stream_orchestrated_chat(
            {"id": "agent", "name": "Agent"},
            {
                "id": "route",
                "mode": "stack",
                "candidates": [
                    {"connection_id": "first"},
                    {"connection_id": "second"},
                ],
            },
            {
                "first": {"id": "first", "name": "First", "type": "openai"},
                "second": {"id": "second", "name": "Second", "type": "openai"},
            },
            [{"role": "user", "content": "task"}],
            None,
        )
    ]
    joined = "".join(frames)
    assert '"type": "routing_failover"' in joined
    assert '"token": "ok"' in joined
    assert '"selected_connection_id": "second"' in joined


@pytest.mark.asyncio
async def test_stack_does_not_fail_over_after_token(monkeypatch):
    called: list[str] = []

    async def fake_stream(_agent, connection, _history, _skills, *args, **kwargs):
        called.append(connection["id"])
        yield _frame({"type": "token", "token": "partial"})
        yield _frame({"type": "error", "message": "disconnected"})

    monkeypatch.setattr("app.services.llm_routing.stream_chat", fake_stream)
    frames = [
        frame
        async for frame in stream_orchestrated_chat(
            {"id": "agent", "name": "Agent"},
            {
                "id": "route",
                "mode": "stack",
                "candidates": [
                    {"connection_id": "first"},
                    {"connection_id": "second"},
                ],
            },
            {
                "first": {"id": "first", "name": "First", "type": "openai"},
                "second": {"id": "second", "name": "Second", "type": "openai"},
            },
            [{"role": "user", "content": "task"}],
            None,
        )
    ]
    assert called == ["first"]
    assert '"message": "disconnected"' in "".join(frames)


@pytest.mark.asyncio
async def test_stack_fails_over_when_provider_raises_before_token(monkeypatch):
    called: list[str] = []

    async def fake_stream(_agent, connection, _history, _skills, *args, **kwargs):
        called.append(connection["id"])
        if connection["id"] == "first":
            raise TimeoutError("provider timeout")
        yield _frame({"type": "done", "reply": "backup", "tokens": {}})

    monkeypatch.setattr("app.services.llm_routing.stream_chat", fake_stream)
    frames = [
        frame
        async for frame in stream_orchestrated_chat(
            {"id": "agent", "name": "Agent"},
            {
                "id": "route",
                "mode": "stack",
                "candidates": [
                    {"connection_id": "first"},
                    {"connection_id": "second"},
                ],
            },
            {
                "first": {"id": "first", "name": "First", "type": "openai"},
                "second": {
                    "id": "second",
                    "name": "Second",
                    "type": "openai",
                },
            },
            [{"role": "user", "content": "task"}],
            None,
        )
    ]
    assert called == ["first", "second"]
    assert '"type": "routing_failover"' in "".join(frames)
    assert '"reply": "backup"' in "".join(frames)


@pytest.mark.asyncio
async def test_stack_does_not_fail_over_when_provider_raises_after_token(monkeypatch):
    called: list[str] = []

    async def fake_stream(_agent, connection, _history, _skills, *args, **kwargs):
        called.append(connection["id"])
        yield _frame({"type": "token", "token": "partial"})
        raise ConnectionError("stream interrupted")

    monkeypatch.setattr("app.services.llm_routing.stream_chat", fake_stream)
    frames = [
        frame
        async for frame in stream_orchestrated_chat(
            {"id": "agent", "name": "Agent"},
            {
                "id": "route",
                "mode": "stack",
                "candidates": [
                    {"connection_id": "first"},
                    {"connection_id": "second"},
                ],
            },
            {
                "first": {"id": "first", "name": "First", "type": "openai"},
                "second": {
                    "id": "second",
                    "name": "Second",
                    "type": "openai",
                },
            },
            [{"role": "user", "content": "task"}],
            None,
        )
    ]
    assert called == ["first"]
    assert '"token": "partial"' in "".join(frames)
    joined = "".join(frames)
    assert '"code": "internal_error"' in joined
    assert "stream interrupted" not in joined


@pytest.mark.asyncio
async def test_balancer_ranking_and_minimal_context(monkeypatch):
    router_prompt = ""

    async def fake_stream(_agent, connection, history, _skills, *args, **kwargs):
        nonlocal router_prompt
        if connection["id"] == "router":
            router_prompt = history[0]["content"]
            reply = json.dumps(
                {
                    "ranking": [
                        {"connection_id": "second", "reason": "best"},
                        {"connection_id": "first", "reason": "backup"},
                    ]
                }
            )
            yield _frame(
                {"type": "done", "reply": reply, "tokens": {"in": 3, "out": 2}}
            )
        else:
            yield _frame({"type": "done", "reply": "ok", "tokens": {"in": 4, "out": 1}})

    monkeypatch.setattr("app.services.llm_routing.stream_chat", fake_stream)
    frames = [
        frame
        async for frame in stream_orchestrated_chat(
            {"id": "agent", "name": "Agent", "system_prompt": "instruction"},
            {
                "id": "route",
                "mode": "balanced",
                "router_connection_id": "router",
                "candidates": [
                    {"connection_id": "first", "routing_hint": "cheap"},
                    {"connection_id": "second", "routing_hint": "code"},
                ],
            },
            {
                "router": {"id": "router", "name": "Router", "type": "openai"},
                "first": {"id": "first", "name": "First", "type": "openai"},
                "second": {"id": "second", "name": "Second", "type": "openai"},
            },
            [
                {"role": "user", "content": "SECRET OLD HISTORY"},
                {"role": "assistant", "content": "old answer"},
                {"role": "user", "content": "current task"},
            ],
            None,
        )
    ]
    joined = "".join(frames)
    assert "SECRET OLD HISTORY" not in router_prompt
    assert "current task" in router_prompt
    assert '"selected_connection_id": "second"' in joined
    assert '"router": {"in": 3, "out": 2}' in joined


@pytest.mark.asyncio
async def test_balanced_fails_when_router_fails(monkeypatch):
    called: list[str] = []

    async def fake_stream(_agent, connection, _history, _skills, *args, **kwargs):
        called.append(connection["id"])
        yield _frame({"type": "error", "message": "router unavailable"})

    monkeypatch.setattr("app.services.llm_routing.stream_chat", fake_stream)
    frames = [
        frame
        async for frame in stream_orchestrated_chat(
            {"id": "agent", "name": "Agent"},
            {
                "id": "route",
                "mode": "balanced",
                "router_connection_id": "router",
                "candidates": [
                    {"connection_id": "first"},
                    {"connection_id": "second"},
                ],
            },
            {
                "router": {"id": "router", "name": "Router", "type": "openai"},
                "first": {"id": "first", "name": "First", "type": "openai"},
                "second": {"id": "second", "name": "Second", "type": "openai"},
            },
            [{"role": "user", "content": "task"}],
            None,
        )
    ]

    joined = "".join(frames)
    assert called == ["router"]
    assert '"type": "routing_warning"' in joined
    assert '"type": "error"' in joined
    assert "orden manual" not in joined


@pytest.mark.asyncio
async def test_balanced_fails_when_router_returns_invalid_ranking(monkeypatch):
    called: list[str] = []

    async def fake_stream(_agent, connection, _history, _skills, *args, **kwargs):
        called.append(connection["id"])
        if connection["id"] == "router":
            yield _frame(
                {
                    "type": "done",
                    "reply": '{"ranking":[{"connection_id":"unknown"}]}',
                    "tokens": {"in": 4, "out": 2},
                }
            )

    monkeypatch.setattr("app.services.llm_routing.stream_chat", fake_stream)
    frames = [
        frame
        async for frame in stream_orchestrated_chat(
            {"id": "agent", "name": "Agent"},
            {
                "id": "route",
                "mode": "balanced",
                "router_connection_id": "router",
                "candidates": [
                    {"connection_id": "first"},
                    {"connection_id": "second"},
                ],
            },
            {
                "router": {"id": "router", "name": "Router", "type": "openai"},
                "first": {"id": "first", "name": "First", "type": "openai"},
                "second": {"id": "second", "name": "Second", "type": "openai"},
            },
            [{"role": "user", "content": "task"}],
            None,
        )
    ]

    joined = "".join(frames)
    assert called == ["router"]
    assert '"type": "error"' in joined
    assert '"router": {"in": 4, "out": 2}' in joined
