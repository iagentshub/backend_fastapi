"""Contratos arquitectónicos del desglose de agents y connections."""

from __future__ import annotations


def _endpoint_modules(app) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for route in app.routes:
        nested = getattr(getattr(route, "original_router", None), "routes", [route])
        for endpoint_route in nested:
            for method in getattr(endpoint_route, "methods", set()):
                result[(method, endpoint_route.path)] = (
                    endpoint_route.endpoint.__module__
                )
    return result


def test_agent_capabilities_use_dedicated_routers(client):
    modules = _endpoint_modules(client.app)
    assert modules[("POST", "/api/agents/{agent_id}/chat")] == (
        "app.api.routes.agent_chat"
    )
    assert modules[("GET", "/api/agents/{agent_id}/export/{fmt}")] == (
        "app.api.routes.agent_exports"
    )
    assert modules[("PUT", "/api/agents/{agent_id}/preferences")] == (
        "app.api.routes.agent_preferences"
    )


def test_connection_capabilities_use_dedicated_routers(client):
    modules = _endpoint_modules(client.app)
    assert modules[("POST", "/api/connections/test-all")] == (
        "app.api.routes.connection_diagnostics"
    )
    assert modules[("POST", "/api/connections/{conn_id}/test")] == (
        "app.api.routes.connection_diagnostics"
    )
    assert modules[("POST", "/api/connections/{conn_id}/hub-sync")] == (
        "app.api.routes.connection_sync"
    )
    assert modules[("POST", "/api/connections/{conn_id}/import-models")] == (
        "app.api.routes.connection_sync"
    )
