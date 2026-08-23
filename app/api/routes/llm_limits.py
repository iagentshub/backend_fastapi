"""Cuotas compartidas de todas las rutas HTTP que pueden gastar LLM."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from app.config.session import (
    RATE_CHAT_CALLS,
    RATE_CHAT_WINDOW,
    RATE_IP_FACTOR,
    RATE_OFFICIAL_LLM_CALLS,
    RATE_OFFICIAL_LLM_WINDOW,
    RATE_WORKFLOW_NODE_CALLS,
    RATE_WORKFLOW_NODE_WINDOW,
    RATE_WORKFLOW_START_CALLS,
    RATE_WORKFLOW_START_WINDOW,
)
from app.middleware.ratelimit import RateLimiter, principal_key
from app.utils.net import client_ip

interactive_llm_limiter = RateLimiter(
    calls=RATE_CHAT_CALLS,
    window=RATE_CHAT_WINDOW,
    key_func=principal_key,
    shared=True,
    name="interactive-llm",
    ip_calls=RATE_CHAT_CALLS * RATE_IP_FACTOR,
)
workflow_start_limiter = RateLimiter(
    calls=RATE_WORKFLOW_START_CALLS,
    window=RATE_WORKFLOW_START_WINDOW,
    key_func=principal_key,
    shared=True,
    name="workflow-start",
    ip_calls=RATE_WORKFLOW_START_CALLS * RATE_IP_FACTOR,
)
workflow_node_limiter = RateLimiter(
    calls=RATE_WORKFLOW_NODE_CALLS,
    window=RATE_WORKFLOW_NODE_WINDOW,
    key_func=principal_key,
    shared=True,
    name="workflow-node",
    ip_calls=RATE_WORKFLOW_NODE_CALLS * RATE_IP_FACTOR,
)
official_llm_limiter = RateLimiter(
    calls=RATE_OFFICIAL_LLM_CALLS,
    window=RATE_OFFICIAL_LLM_WINDOW,
    key_func=principal_key,
    shared=True,
    name="official-source-llm",
    ip_calls=RATE_OFFICIAL_LLM_CALLS * RATE_IP_FACTOR,
)


def workflow_node_quota(request: Request) -> Callable[[int], Awaitable[None]]:
    """Captura solo la clave estable; no retiene Request en la tarea de fondo."""
    key = principal_key(request)
    ip = client_ip(request)

    async def consume(cost: int) -> None:
        await workflow_node_limiter.consume_key(key, cost=cost, ip_key=ip)

    return consume
