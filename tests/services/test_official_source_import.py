from __future__ import annotations

import asyncio

import pytest

from app.api.routes.admin import official_sources as official_source_routes
from app.models.official_source import PackageComponent
from app.services.official_source_drafts import OfficialImportDraftService
from app.services.official_source_importer import (
    detect_components,
    parse_repository_url,
)
from app.services.official_source_sync import OfficialSourceMaterializer
from app.storage.official_source_storage import OfficialSourceStorage


def test_inspect_does_not_persist_source_but_legacy_import_does(monkeypatch) -> None:
    draft = {
        "id": "draft-id",
        "source": {
            "name": "Demo",
            "repository_url": "https://github.com/example/demo",
        },
        "resolved_version": "v1",
        "commit_sha": "abc123",
    }
    saved: list[dict] = []

    class FakeDrafts:
        async def inspect(self, *_args, **_kwargs):
            return draft

    class FakeStorage:
        async def save_source(self, payload):
            saved.append(payload)
            return {**payload, "id": "source-id", "last_commit_sha": ""}

        async def attach_draft_source(self, _draft_id, source):
            return {**draft, "source_id": source["id"], "source": source}

    async def payload(value, **_kwargs):
        return value

    monkeypatch.setattr(official_source_routes, "_drafts", FakeDrafts())
    monkeypatch.setattr(official_source_routes, "_storage", FakeStorage())
    monkeypatch.setattr(official_source_routes, "_draft_payload", payload)
    body = official_source_routes.ImportSourceBody(
        repository_url="https://github.com/example/demo"
    )

    inspected = asyncio.run(
        official_source_routes.admin_inspect_official_source(body, "admin-id")
    )
    assert inspected["id"] == "draft-id"
    assert saved == []

    imported = asyncio.run(
        official_source_routes.admin_import_official_source(body, "admin-id")
    )
    assert imported["source_id"] == "source-id"
    assert saved[0]["owner_id"] == "admin-id"


def test_parse_gitlab_nested_group() -> None:
    parsed = parse_repository_url("https://gitlab.com/company/platform/team/agents.git")

    assert parsed == {
        "provider": "gitlab",
        "repository_path": "company/platform/team/agents",
        "repository_owner": "company/platform/team",
        "repository_name": "agents",
        "repository_url": "https://gitlab.com/company/platform/team/agents",
    }


def test_detector_prefers_canonical_roots_and_ignores_actions_and_docs() -> None:
    files = {
        "agents/reviewer.md": "---\nname: Reviewer\n---\nReview",
        "plugins/demo/agents/reviewer.md": "---\nname: Reviewer\n---\nReview",
        "plugins/demo/agents/plugin-only.md": "Plugin copy",
        "skills/check/SKILL.md": "---\nname: Check\n---\nCheck",
        ".openclaw/skills/check/SKILL.md": "---\nname: Check\n---\nCheck",
        "commands/plan.md": "Plan",
        ".github/workflows/ci.yml": "jobs: {test: {}}",
        "docs/agents/fake.md": "Documentation",
        "tests/skills/fake/SKILL.md": "Fixture",
    }

    components = detect_components("source", files)

    assert [(item.component_type, item.component_id) for item in components] == [
        ("agent", "reviewer"),
        ("command", "plan"),
        ("skill", "check"),
    ]
    reviewer = components[0]
    assert reviewer.variants == ["plugins/demo/agents/reviewer.md"]


def test_first_draft_is_empty_and_apply_creates_normal_resources(
    admin_client,
) -> None:
    admin_id = next(
        user["id"]
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )

    class FakeImporter:
        async def inspect_repository(self, *_args, **_kwargs):
            return {
                "source": {
                    "id": "draft",
                    "name": "Demo",
                    "description": "",
                    "repository_url": "https://github.com/example/demo",
                    "repository_owner": "example",
                    "repository_name": "demo",
                    "repository_path": "example/demo",
                    "provider": "github",
                    "default_branch": "develop",
                    "tracking_mode": "branch",
                    "tracking_ref": "develop",
                    "license": "MIT",
                },
                "version": "abc123",
                "commit_sha": "abc123456",
                "errors": [],
                "security_warnings": [],
                "components": [
                    PackageComponent(
                        source_id="draft",
                        component_id="guide",
                        component_type="knowledge",
                        name="Guide",
                        source_path="knowledge/guide.md",
                        content="# Guide",
                        content_hash="hash-guide",
                    )
                ],
            }

    async def run():
        storage = OfficialSourceStorage()
        service = OfficialImportDraftService(storage, importer=FakeImporter())
        draft = await service.inspect(
            "https://github.com/example/demo",
            admin_id,
            tracking_mode="branch",
            tracking_ref="develop",
        )
        assert await storage.list_sources() == []
        items = await storage.get_all_draft_components(draft["id"])
        assert items[0]["selected"] is False
        await service.update_component(draft["id"], "guide", {"selected": True})
        applied = await service.apply(draft["id"], admin_id)
        source = (await storage.list_sources())[0]
        origin = await storage.get_origin(
            "knowledge", applied["resources"][0]["resource_id"], admin_id
        )
        return source, origin

    source, origin = asyncio.run(run())
    assert source["owner_id"] == admin_id
    assert source["default_branch"] == "develop"
    assert origin is not None
    assert origin["source_path"] == "knowledge/guide.md"
    assert origin["commit_sha"] == "abc123456"


def test_deselect_dependency_cascades_to_agent(admin_client) -> None:
    async def run():
        admin_id = next(
            user["id"]
            for user in admin_client.get("/api/admin/users").json()
            if user["username"] == "testadmin"
        )
        storage = OfficialSourceStorage()
        source = {
            "repository_url": "https://github.com/example/cascade",
            "provider": "github",
            "repository_path": "example/cascade",
            "tracking_mode": "branch",
            "tracking_ref": "main",
            "resolved_version": "v1",
            "commit_sha": "sha",
            "name": "Cascade",
        }
        components = [
            {
                **PackageComponent(
                    source_id="draft",
                    component_id="skill",
                    component_type="skill",
                    name="Skill",
                    source_path="skills/skill/SKILL.md",
                    content_hash="one",
                ).as_dict(include_content=True),
                "selected": False,
                "state": "new",
            },
            {
                **PackageComponent(
                    source_id="draft",
                    component_id="agent",
                    component_type="agent",
                    name="Agent",
                    source_path="agents/agent.md",
                    content_hash="two",
                    dependencies=["skill"],
                ).as_dict(include_content=True),
                "selected": False,
                "state": "new",
            },
        ]
        draft = await storage.create_draft(
            owner_id=admin_id, source=source, components=components
        )
        service = OfficialImportDraftService(storage)
        await service.update_component(draft["id"], "agent", {"selected": True})
        graph = await service.graph(draft["id"])
        selected = {
            item["component_id"]
            for item in await storage.get_all_draft_components(draft["id"])
            if item["selected"]
        }
        await service.update_component(draft["id"], "skill", {"selected": False})
        after = {
            item["component_id"]
            for item in await storage.get_all_draft_components(draft["id"])
            if item["selected"]
        }
        return selected, after, graph

    selected, after, graph = asyncio.run(run())
    assert selected == {"agent", "skill"}
    assert after == set()
    assert graph["root_id"] == "source"
    assert graph["nodes"][0]["label"] == "Cascade"
    assert {
        (edge["source_id"], edge["target_id"], edge["dashed"])
        for edge in graph["edges"]
    } >= {("source", "agent", False), ("agent", "skill", True)}


def test_materialization_rolls_back_all_resources_on_failure(admin_client) -> None:
    admin_id = next(
        user["id"]
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )

    async def run():
        storage = OfficialSourceStorage()
        source = await storage.save_source(
            {
                "name": "Rollback",
                "repository_url": "https://github.com/example/rollback",
                "repository_owner": "example",
                "repository_name": "rollback",
                "repository_path": "example/rollback",
                "owner_id": admin_id,
            }
        )
        components = [
            PackageComponent(
                source_id=source["id"],
                component_id="first",
                component_type="skill",
                name="First",
                source_path="skills/first/SKILL.md",
                content="# First",
                content_hash="one",
            ),
            PackageComponent(
                source_id=source["id"],
                component_id="second",
                component_type="knowledge",
                name="Second",
                source_path="knowledge/second.md",
                content="# Second",
                content_hash="two",
            ),
        ]
        materializer = OfficialSourceMaterializer(storage)
        original = materializer._save
        calls = 0

        async def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("induced")
            return await original(*args, **kwargs)

        materializer._save = fail_second  # type: ignore[method-assign]
        try:
            await materializer.materialize(source, components, None, admin_id)
        except RuntimeError as exc:
            assert str(exc) == "induced"
        else:
            raise AssertionError("the induced failure was not raised")
        return await storage.list_resources(source["id"])

    assert asyncio.run(run()) == []


def test_executable_requires_individual_review_before_apply(admin_client) -> None:
    admin_id = next(
        user["id"]
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )

    async def run():
        storage = OfficialSourceStorage()
        source = {
            "repository_url": "https://github.com/example/tool-review",
            "provider": "github",
            "repository_path": "example/tool-review",
            "repository_owner": "example",
            "repository_name": "tool-review",
            "tracking_mode": "branch",
            "tracking_ref": "main",
            "resolved_version": "v1",
            "commit_sha": "sha",
            "name": "Tool review",
        }
        tool = PackageComponent(
            source_id="draft",
            component_id="safe-tool",
            component_type="tool",
            name="Safe tool",
            source_path="tools/safe.py",
            content="print('safe')",
            content_hash="tool-hash",
            executable=True,
            security_review_required=True,
        )
        draft = await storage.create_draft(
            owner_id=admin_id,
            source=source,
            components=[
                {
                    **tool.as_dict(include_content=True),
                    "selected": True,
                    "explicitly_selected": True,
                    "state": "new",
                }
            ],
        )
        service = OfficialImportDraftService(storage)
        with pytest.raises(ValueError, match="selected_tool_requires_review"):
            await service.apply(draft["id"], admin_id)
        assert await storage.list_sources() == []
        await storage.update_draft_component(
            draft["id"], "safe-tool", {"security_accepted": True}
        )
        return await service.apply(draft["id"], admin_id)

    result = asyncio.run(run())
    assert result["resources"][0]["resource_type"] == "tool"
