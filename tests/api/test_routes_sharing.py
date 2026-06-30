"""Tests de /api/sharing — compartir recursos con grupos de workspace."""
from __future__ import annotations

import asyncio


# ── Auth requerida ────────────────────────────────────────────────────────────


def test_get_sharing_requires_auth(client):
    r = client.get("/api/sharing/agent/resource-id")
    assert r.status_code == 401


def test_post_sharing_requires_auth(client):
    r = client.post("/api/sharing/agent/resource-id", json={"group_id": "gid"})
    assert r.status_code == 401


def test_delete_sharing_requires_auth(client):
    r = client.delete("/api/sharing/agent/resource-id/group-id")
    assert r.status_code == 401


def test_by_group_requires_auth(client):
    r = client.get("/api/sharing/by-group/group-id/agent")
    assert r.status_code == 401


# ── Validación de tipo de recurso ─────────────────────────────────────────────


def test_invalid_resource_type_on_get_returns_422(admin_client):
    r = admin_client.get("/api/sharing/invalidtype/resource-id")
    assert r.status_code == 422


def test_invalid_resource_type_on_post_returns_422(admin_client):
    r = admin_client.post("/api/sharing/badtype/resource-id", json={"group_id": "gid"})
    assert r.status_code == 422


def test_invalid_resource_type_on_by_group_returns_422(admin_client):
    r_create = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "type-check-group"}
    )
    assert r_create.status_code == 200
    group_id = r_create.json()["id"]
    r = admin_client.get(f"/api/sharing/by-group/{group_id}/badtype")
    assert r.status_code == 422


# ── GET compartición de recurso ───────────────────────────────────────────────


def test_get_sharing_initially_empty(admin_client):
    r = admin_client.get("/api/sharing/agent/recurso-vacio-xyz")
    assert r.status_code == 200
    assert r.json() == []


# ── POST compartir recurso ────────────────────────────────────────────────────


def test_share_resource_success(admin_client):
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "share-success-group"}
    )
    assert r.status_code == 200
    group_id = r.json()["id"]

    r = admin_client.post("/api/sharing/agent/my-agent-abc", json={"group_id": group_id})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_shared_resource_appears_in_get(admin_client):
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "appear-group"}
    )
    group_id = r.json()["id"]
    admin_client.post("/api/sharing/agent/agent-appear", json={"group_id": group_id})

    r = admin_client.get("/api/sharing/agent/agent-appear")
    assert r.status_code == 200
    items = r.json()
    assert any(item["group_id"] == group_id for item in items)


def test_share_missing_group_id_returns_422(admin_client):
    r = admin_client.post("/api/sharing/agent/res-nogroupid", json={})
    assert r.status_code == 422


def test_share_nonexistent_group_returns_404(admin_client):
    r = admin_client.post(
        "/api/sharing/agent/res-404group", json={"group_id": "grupo-que-no-existe"}
    )
    assert r.status_code == 404


def test_share_group_from_different_workspace_rejected(admin_client):
    """Un usuario no puede compartir usando un grupo que pertenece a otro workspace."""
    from app.auth.auth import create_token, register_user

    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "cross-ws-group"}
    )
    assert r.status_code == 200
    group_id = r.json()["id"]

    asyncio.run(register_user("cross_alice", "pass1234", email="cross_alice@test.com"))
    admin_client.cookies.set("ga_token", create_token("cross_alice"))

    r = admin_client.post(
        "/api/sharing/agent/alice-resource", json={"group_id": group_id}
    )
    assert r.status_code == 403


# ── DELETE dejar de compartir ─────────────────────────────────────────────────


def test_unshare_resource_success(admin_client):
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "unshare-group"}
    )
    group_id = r.json()["id"]
    admin_client.post("/api/sharing/connection/conn-xyz", json={"group_id": group_id})

    r = admin_client.delete(f"/api/sharing/connection/conn-xyz/{group_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_unshare_removes_from_list(admin_client):
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "remove-list-group"}
    )
    group_id = r.json()["id"]
    admin_client.post("/api/sharing/skill/skill-remove", json={"group_id": group_id})
    admin_client.delete(f"/api/sharing/skill/skill-remove/{group_id}")

    r = admin_client.get("/api/sharing/skill/skill-remove")
    assert r.status_code == 200
    assert r.json() == []


# ── GET recursos por grupo ────────────────────────────────────────────────────


def test_get_group_resources_empty(admin_client):
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "empty-res-group"}
    )
    group_id = r.json()["id"]

    r = admin_client.get(f"/api/sharing/by-group/{group_id}/agent")
    assert r.status_code == 200
    assert r.json() == []


def test_get_group_resources_after_share(admin_client):
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "resources-group"}
    )
    group_id = r.json()["id"]
    admin_client.post("/api/sharing/knowledge/kb-item-1", json={"group_id": group_id})

    r = admin_client.get(f"/api/sharing/by-group/{group_id}/knowledge")
    assert r.status_code == 200
    items = r.json()
    assert any(item["resource_id"] == "kb-item-1" for item in items)


def test_get_group_resources_nonexistent_group(admin_client):
    r = admin_client.get("/api/sharing/by-group/grupoinexistente/agent")
    assert r.status_code == 404


def test_non_member_cannot_read_group_resources(admin_client):
    """Un usuario sin membresía en el grupo ni gestión del workspace recibe 403."""
    from app.auth.auth import create_token, register_user

    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "restricted-group"}
    )
    group_id = r.json()["id"]
    admin_client.post("/api/sharing/agent/restricted-res", json={"group_id": group_id})

    asyncio.run(register_user("outsider_user", "pass1234", email="outsider_user@test.com"))
    admin_client.cookies.set("ga_token", create_token("outsider_user"))

    r = admin_client.get(f"/api/sharing/by-group/{group_id}/agent")
    assert r.status_code == 403


def test_share_multiple_resource_types_in_one_group(admin_client):
    """Se pueden compartir recursos de distintos tipos con el mismo grupo."""
    r = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "multi-type-group"}
    )
    group_id = r.json()["id"]

    admin_client.post("/api/sharing/agent/agent-type-1", json={"group_id": group_id})
    admin_client.post("/api/sharing/agent/agent-type-2", json={"group_id": group_id})
    admin_client.post("/api/sharing/skill/skill-type-1", json={"group_id": group_id})

    r_agents = admin_client.get(f"/api/sharing/by-group/{group_id}/agent")
    assert r_agents.status_code == 200
    assert len(r_agents.json()) >= 2

    r_skills = admin_client.get(f"/api/sharing/by-group/{group_id}/skill")
    assert r_skills.status_code == 200
    assert len(r_skills.json()) >= 1


def test_resource_shared_with_multiple_groups(admin_client):
    """Un recurso puede compartirse con varios grupos a la vez."""
    r1 = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "multi-group-a"}
    )
    r2 = admin_client.post(
        "/api/workspaces/testadmin/groups", json={"name": "multi-group-b"}
    )
    group_a = r1.json()["id"]
    group_b = r2.json()["id"]

    admin_client.post("/api/sharing/agent/shared-everywhere", json={"group_id": group_a})
    admin_client.post("/api/sharing/agent/shared-everywhere", json={"group_id": group_b})

    r = admin_client.get("/api/sharing/agent/shared-everywhere")
    assert r.status_code == 200
    group_ids = {item["group_id"] for item in r.json()}
    assert group_a in group_ids
    assert group_b in group_ids
