from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.pagination.metrics import reset_for_tests, snapshot
from app.pagination.total import ExactTotalTimeout


def _create_agents(client, count: int) -> None:
    for index in range(count):
        response = client.post("/api/agents", json={"name": f"V2 {index}"})
        assert response.status_code == 200, response.text


def test_v2_page_is_typed_self_contained_and_snapshot_consistent(admin_client):
    _create_agents(admin_client, 3)
    first = admin_client.get(
        "/api/v2/agents", params={"limit": 2, "include_total": "true"}
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) == {"items", "page"}
    assert len(body["items"]) == 2
    assert body["page"]["limit"] == 2
    assert body["page"]["has_more"] is True
    assert body["page"]["next_cursor"]
    assert body["page"]["total"] == 3
    assert body["page"]["snapshot_at"]

    second = admin_client.get(
        "/api/v2/agents",
        params={
            "limit": 2,
            "include_total": "true",
            "cursor": body["page"]["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["page"]["snapshot_at"] == body["page"]["snapshot_at"]
    assert second.json()["page"]["total"] == 3


def test_exact_total_is_computed_once_per_cursor_walk(admin_client):
    reset_for_tests()
    _create_agents(admin_client, 3)
    first = admin_client.get(
        "/api/v2/agents", params={"limit": 1, "include_total": "true"}
    ).json()
    second = admin_client.get(
        "/api/v2/agents",
        params={
            "limit": 1,
            "include_total": "true",
            "cursor": first["page"]["next_cursor"],
        },
    )
    assert second.status_code == 200
    agent_metrics = snapshot()["agent"]
    assert agent_metrics["total_queries"] == 1
    assert agent_metrics["total_from_cursor"] == 1
    assert agent_metrics["max_page_number"] == 2


def test_signed_cursor_keeps_total_when_an_intermediate_page_omits_it(admin_client):
    reset_for_tests()
    _create_agents(admin_client, 4)
    first = admin_client.get(
        "/api/v2/agents", params={"limit": 1, "include_total": "true"}
    ).json()
    second = admin_client.get(
        "/api/v2/agents",
        params={"limit": 1, "cursor": first["page"]["next_cursor"]},
    ).json()
    assert second["page"]["total"] is None

    third = admin_client.get(
        "/api/v2/agents",
        params={
            "limit": 1,
            "include_total": "true",
            "cursor": second["page"]["next_cursor"],
        },
    )
    assert third.status_code == 200
    assert third.json()["page"]["total"] == 4
    metrics = snapshot()["agent"]
    assert metrics["total_queries"] == 1
    assert metrics["total_from_cursor"] == 1


def test_consistency_can_be_disabled_explicitly(admin_client):
    _create_agents(admin_client, 1)
    response = admin_client.get("/api/v2/agents", params={"consistent": "false"})
    assert response.status_code == 200
    assert response.json()["page"]["snapshot_at"] is None


def test_total_timeout_is_a_stable_503(admin_client):
    with patch(
        "app.storage.cursor_page_query.exact_total",
        new=AsyncMock(side_effect=ExactTotalTimeout),
    ):
        response = admin_client.get("/api/v2/agents", params={"include_total": "true"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "pagination_total_timeout"


def test_v2_exists_for_all_cursor_catalogs(admin_client):
    for resource in (
        "agents",
        "skills",
        "prompts",
        "tools",
        "knowledge",
        "knowledge-packs",
    ):
        response = admin_client.get(f"/api/v2/{resource}")
        assert response.status_code == 200, (resource, response.text)
        assert set(response.json()) == {"items", "page"}


def test_knowledge_v2_pages_with_a_signed_cursor(admin_client):
    for index in range(3):
        response = admin_client.post(
            "/api/knowledge/text",
            json={"title": f"Documento {index}", "content": "contenido"},
        )
        assert response.status_code == 200, response.text

    first = admin_client.get("/api/v2/knowledge", params={"limit": 2}).json()
    assert len(first["items"]) == 2
    assert first["page"]["has_more"] is True
    assert first["page"]["next_cursor"]

    second = admin_client.get(
        "/api/v2/knowledge",
        params={"limit": 2, "cursor": first["page"]["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    assert len(second.json()["items"]) == 1
    assert second.json()["page"]["has_more"] is False


def test_removed_legacy_list_gets_leave_openapi(admin_client):
    assert admin_client.get("/api/agents/import/catalog/skill").status_code == 404
    schema = admin_client.app.openapi()
    resources = (
        "/api/agents",
        "/api/skills",
        "/api/prompts",
        "/api/tools",
        "/api/knowledge",
        "/api/knowledge/packs",
        "/api/explore",
        "/api/users",
        "/api/admin/logs",
        "/api/feed",
        "/api/connections",
        "/api/admin/explore",
        "/api/admin/metadata/tables/{table_name}/data",
    )
    for path in resources:
        assert "get" not in schema.get("paths", {}).get(path, {})


def test_cursor_v2_exists_for_completed_catalogs(admin_client):
    endpoints = (
        "/api/v2/feed",
        "/api/v2/connections",
        "/api/v2/admin/explore",
        "/api/v2/admin/metadata/tables/users/data",
    )
    for endpoint in endpoints:
        response = admin_client.get(endpoint, params={"limit": 2})
        assert response.status_code == 200, (endpoint, response.text)
        body = response.json()
        assert "page" in body
        assert body["page"]["limit"] == 2
        assert "has_more" in body["page"]
        assert "next_cursor" in body["page"]


def test_connections_v2_keeps_models_nested_and_never_exposes_secrets(admin_client):
    created = admin_client.post(
        "/api/connections",
        json={
            "name": "Cursor Ollama",
            "type": "ollama",
            "host": "http://localhost:11434",
            "api_key": "secret-value",
        },
    )
    assert created.status_code == 200, created.text

    with patch(
        "app.connections.ollama.OllamaProvider.fetch_models",
        return_value=["llama3", "mistral"],
    ) as fetch_models:
        ordinary = admin_client.get("/api/v2/connections")
        assert ordinary.status_code == 200, ordinary.text
        fetch_models.assert_not_called()
        expanded = admin_client.get(
            "/api/v2/connections", params={"include_models": "true"}
        )

    assert expanded.status_code == 200, expanded.text
    item = expanded.json()["items"][0]
    assert "api_key" not in item
    assert [variant["model"] for variant in item["model_variants"]] == [
        "llama3",
        "mistral",
    ]


def test_admin_explore_v2_filters_before_hydration(admin_client):
    created = admin_client.post("/api/agents", json={"name": "Unique cursor agent"})
    assert created.status_code == 200, created.text

    response = admin_client.get(
        "/api/v2/admin/explore",
        params={
            "type": "agent",
            "q": "Unique cursor agent",
            "include_counts": "true",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["resource_type"] for item in body["items"]] == ["agent"]
    assert body["counts"]["agent"] == 1


def test_admin_stats_exposes_bounded_pagination_metrics(admin_client):
    reset_for_tests()
    response = admin_client.get("/api/v2/agents")
    assert response.status_code == 200
    stats = admin_client.get("/api/admin/stats")
    assert stats.status_code == 200
    metrics = stats.json()["pagination"]["agent"]
    assert metrics["requests"] == 1
    assert metrics["max_page_number"] == 1
    assert "duration_ms_avg" in metrics
