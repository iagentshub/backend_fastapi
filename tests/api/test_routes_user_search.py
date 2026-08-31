"""Tests for GET /api/users (user search endpoint)."""

from __future__ import annotations


def _login(client, username: str, password: str = "pass1234") -> str:
    import asyncio

    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, password, email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _register(username: str, password: str = "pass1234") -> None:
    import asyncio

    from app.auth.auth import register_user

    asyncio.run(register_user(username, password, email=f"{username}@example.com"))


def test_search_users_returns_list_excluding_self(client):
    """GET /api/users returns other users but NOT the authenticated user."""
    _login(client, "srch_me")
    _register("srch_alice")
    _register("srch_bob")

    r = client.get("/api/v2/users")
    assert r.status_code == 200
    data = r.json()["items"]
    assert isinstance(data, list)

    usernames = [u["username"] for u in data]
    assert "srch_alice" in usernames
    assert "srch_bob" in usernames
    assert "srch_me" not in usernames

    # Each item must have the expected fields
    for item in data:
        assert "username" in item
        assert "avatar_url" in item
        assert "followers_count" in item
        assert "public_resources_count" in item


def test_search_users_filters_by_q(client):
    """GET /api/users?q=ali only returns users whose username contains 'ali'."""
    _login(client, "fltr_searcher")
    _register("fltr_alice")
    _register("fltr_bob")

    r = client.get("/api/v2/users?q=ali")
    assert r.status_code == 200
    data = r.json()["items"]
    assert isinstance(data, list)

    usernames = [u["username"] for u in data]
    assert "fltr_alice" in usernames
    assert "fltr_bob" not in usernames


def test_search_users_unauthenticated_returns_401(client):
    """GET /api/users without a session cookie returns 401."""
    client.cookies.clear()
    r = client.get("/api/v2/users")
    assert r.status_code == 401


def test_search_users_no_match_returns_empty_list(client):
    """GET /api/users?q=nonexistent returns an empty list."""
    _login(client, "empty_searcher")
    _register("empty_other")

    r = client.get("/api/v2/users?q=zzz_nonexistent_xyz_99")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_search_users_rejects_negative_pagination(client):
    _login(client, "pagevalidator")
    assert client.get("/api/v2/users?limit=-1").status_code == 422
    assert client.get("/api/v2/users?offset=-1").status_code == 422


def test_search_users_v2_cursor_and_optional_total(client):
    _login(client, "v2_searcher")
    for suffix in ("anna", "bea", "carla"):
        _register(f"v2_{suffix}")

    first = client.get(
        "/api/v2/users", params={"q": "v2_", "limit": 2, "include_total": True}
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["page"]["total"] == 3
    assert payload["page"]["has_more"] is True
    assert payload["page"]["next_cursor"]

    second = client.get(
        "/api/v2/users",
        params={
            "q": "v2_",
            "limit": 2,
            "include_total": True,
            "cursor": payload["page"]["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["page"]["total"] == 3
    assert {item["username"] for item in payload["items"]}.isdisjoint(
        item["username"] for item in second.json()["items"]
    )


def test_search_users_v2_rejects_offset_and_filter_reuse(client):
    _login(client, "v2_guard")
    _register("v2_guard_a")
    _register("v2_guard_b")
    assert client.get("/api/v2/users?offset=1").status_code == 422
    first = client.get("/api/v2/users?q=guard&limit=1").json()
    cursor = first["page"]["next_cursor"]
    assert cursor
    changed = client.get(
        "/api/v2/users", params={"q": "another", "limit": 1, "cursor": cursor}
    )
    assert changed.status_code == 422
    assert changed.json()["detail"]["code"] == "invalid_cursor"
