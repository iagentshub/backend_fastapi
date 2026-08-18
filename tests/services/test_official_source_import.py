from __future__ import annotations

import asyncio
import json

import pytest

from app.api.routes.admin import official_sources as official_source_routes
from app.models.official_source import PackageComponent
from app.services.official_source_drafts import OfficialImportDraftService
from app.services.official_source_importer import (
    detect_components,
    parse_repository_url,
    validate_components,
)
from app.services.official_source_llm import OfficialSourceLLMAnalyzer
from app.services.official_source_sync import OfficialSourceMaterializer
from app.storage.db import open_db
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
        "agents/reviewer.md": "---\nname: Reviewer\ndescription: Reviews code\n---\nReview",
        "plugins/demo/agents/reviewer.md": "---\nname: Reviewer\ndescription: Reviews code\n---\nReview",
        "plugins/demo/agents/plugin-only.md": "Plugin copy",
        "skills/check/SKILL.md": "---\nname: Check\n---\nCheck",
        ".openclaw/skills/check/SKILL.md": "---\nname: Check\n---\nCheck",
        "commands/plan.md": "Plan",
        ".github/workflows/ci.yml": "jobs: {test: {}}",
        "docs/agents/fake.md": "Documentation",
        "tests/skills/fake/SKILL.md": "Fixture",
    }

    components = detect_components("source", files)

    assert [
        (item.component_type, item.component_id)
        for item in components
        if item.component_type != "unknown"
    ] == [
        ("agent", "reviewer"),
        ("command", "plan"),
        ("skill", "check"),
    ]
    reviewer = components[0]
    assert reviewer.variants == ["plugins/demo/agents/reviewer.md"]


def test_detector_links_agents_to_resources_from_structured_references() -> None:
    files = {
        "agents/evaluator.md": (
            "---\nname: Evaluator\ntools: [Read, Write, Bash]\n---\n"
            "Read `skills/evaluation/SKILL.md` before scoring. Use $build when needed."
        ),
        "agents/investigator.md": (
            "---\nname: Investigator\ndescription: Investigates\n---\nInvestigate only."
        ),
        "skills/evaluation/SKILL.md": "---\nname: Evaluation\n---\nRubric",
        "skills/build/SKILL.md": "---\nname: Build\n---\nBuild safely",
        "skills/cavecrew/SKILL.md": (
            "---\nname: Cavecrew\n---\nDelegate to `investigator`."
        ),
    }

    components = detect_components("source", files)
    by_id = {item.component_id: item for item in components}

    assert by_id["evaluator"].dependencies == ["evaluation", "build"]
    assert by_id["investigator"].dependencies == []
    assert by_id["cavecrew"].relations == [
        {"target_id": "investigator", "relation_type": "orchestrates"}
    ]
    assert "read" not in by_id["evaluator"].dependencies
    assert "write" not in by_id["evaluator"].dependencies
    assert "bash" not in by_id["evaluator"].dependencies


def test_detector_resolves_manifest_relations_and_ignores_ambiguous_aliases() -> None:
    files = {
        "iagentshub.json": json.dumps(
            {
                "components": [
                    {
                        "source_path": "agents/native.md",
                        "type": "agent",
                        "skills": ["skill:native"],
                    }
                ]
            }
        ),
        "agents/native.md": "---\nname: Native\n---\nUse $shared only if available.",
        "skills/native/SKILL.md": "---\nname: Native skill\n---\nNative",
        "skills/one/SKILL.md": "---\nname: Shared\n---\nOne",
        "skills/two/SKILL.md": "---\nname: Shared\n---\nTwo",
    }

    components = detect_components("source", files)
    native = next(item for item in components if item.component_type == "agent")
    native_skill = next(
        item for item in components if item.source_path == "skills/native/SKILL.md"
    )

    assert native.dependencies == [native_skill.component_id]
    assert all(
        item.source_path not in {"skills/one/SKILL.md", "skills/two/SKILL.md"}
        for item in components
        if item.component_id in native.dependencies
    )


def test_external_markdown_reference_is_a_collapsible_log_notice() -> None:
    component = PackageComponent(
        source_id="draft",
        component_id="reviewer",
        component_type="agent",
        name="Reviewer",
        source_path="agents/reviewer.md",
        content="[Guía externa](../../shared/guide.md)",
        content_hash="hash-reviewer",
    )

    errors, notices = validate_components([component])

    assert errors == []
    assert notices == [
        {
            "level": "log",
            "code": "external_markdown_reference",
            "message": (
                "reviewer: referencia fuera del repositorio "
                "(../../shared/guide.md)"
            ),
        }
    ]


def test_first_draft_is_empty_and_apply_creates_normal_resources(
    admin_client,
) -> None:
    admin_id = next(
        user["id"]
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )

    class FakeImporter:
        async def inspect_snapshot(self, *_args, **_kwargs):
            return {"snapshot": True}

        def analyze_snapshot(self, _snapshot):
            return self._payload()

        async def inspect_repository(self, *_args, **_kwargs):
            return self._payload()

        @staticmethod
        def _payload():
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
        graph = await service.relations(draft["id"])
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
    # El borrador entrega hechos, no un grafo: el repositorio como raíz, un
    # componente por fila y la dependencia que lo arrastra.
    assert graph["root"]["label"] == "Cascade"
    hechos = [(item["id"], item["relation"], item["via"]) for item in graph["items"]]
    assert ("agent", "origin", None) in hechos
    assert ("skill", "depends", {"type": "agent", "id": "agent"}) in hechos


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


def test_materialization_replaces_agent_dependencies_with_real_resource_ids(
    admin_client,
) -> None:
    admin_id = next(
        user["id"]
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )

    async def run():
        storage = OfficialSourceStorage()
        source = await storage.save_source(
            {
                "name": "Relations",
                "repository_url": "https://github.com/example/relations",
                "repository_owner": "example",
                "repository_name": "relations",
                "repository_path": "example/relations",
                "owner_id": admin_id,
            }
        )
        components = [
            PackageComponent(
                source_id=source["id"],
                component_id="review-skill",
                component_type="skill",
                name="Review skill",
                source_path="skills/review/SKILL.md",
                content="# Review",
                content_hash="skill-hash",
            ),
            PackageComponent(
                source_id=source["id"],
                component_id="review-agent",
                component_type="agent",
                name="Review agent",
                source_path="agents/review.md",
                content="Review changes",
                content_hash="agent-hash",
                dependencies=["review-skill"],
            ),
        ]
        result = await OfficialSourceMaterializer(storage).materialize(
            source,
            components,
            ["review-agent"],
            admin_id,
        )
        resources = {
            item["resource_type"]: item["resource_id"] for item in result["resources"]
        }
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT data FROM agents WHERE id=? AND owner_id=?",
                (resources["agent"], admin_id),
            )
        assert row is not None
        return json.loads(row["data"]), resources

    agent, resources = asyncio.run(run())
    assert agent["skills"] == [resources["skill"]]


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
            draft["id"],
            "safe-tool",
            {"security_accepted": True, "forced_tool_language": "python"},
        )
        updated = await storage.get_draft_component(draft["id"], "safe-tool")
        assert updated["forced_tool_language"] == "python"
        return await service.apply(draft["id"], admin_id)

    result = asyncio.run(run())
    assert result["resources"][0]["resource_type"] == "tool"


def test_llm_manifest_uses_real_files_and_typed_relations() -> None:
    class FakeConnections:
        async def get(self, connection_id, owner_id):
            assert connection_id == "admin-llm"
            assert owner_id is None
            return {
                "id": connection_id,
                "name": "Admin LLM",
                "type": "openai",
                "model": "test-model",
                "is_active": True,
            }

    class FakeAnalyzer(OfficialSourceLLMAnalyzer):
        async def _invoke(self, connection, prompt):
            assert "agents/reviewer.md" in prompt
            assert "skills/review/SKILL.md" in prompt
            assert "engine/testdata/noise.expected.json" not in prompt
            return json.dumps(
                {
                    "schema_version": "1",
                    "components": [
                        {
                            "id": "reviewer",
                            "resource_type": "agent",
                            "name": "Reviewer",
                            "source_path": "agents/reviewer.md",
                            "language": "python",
                        },
                        {
                            "id": "review",
                            "resource_type": "skill",
                            "name": "Review",
                            "source_path": "skills/review/SKILL.md",
                            "language": "english",
                        },
                        {
                            "id": "review-plugin-copy",
                            "resource_type": "skill",
                            "name": "Review",
                            "source_path": "plugins/demo/skills/review/SKILL.md",
                            "language": "english",
                        },
                        {
                            "id": "hook",
                            "resource_type": "ignore",
                            "name": "Hook",
                            "source_path": "hooks/install.js",
                        },
                    ],
                    "relations": [
                        {
                            "from_id": "reviewer",
                            "to_id": "review",
                            "relation_type": "uses",
                            "evidence_path": "agents/reviewer.md",
                        }
                    ],
                }
            )

    async def run():
        snapshot = {
            "source": {
                "id": "draft",
                "name": "Demo",
                "repository_url": "https://github.com/example/demo",
            },
            "version": "v1",
            "commit_sha": "abc123",
            "files": {
                "agents/reviewer.md": "Use the review skill.",
                "skills/review/SKILL.md": "# Review",
                "plugins/demo/skills/review/SKILL.md": "# Review",
                "hooks/install.js": "console.log('hook')",
                "engine/testdata/noise.expected.json": '{"noise": true}',
            },
        }
        analyzer = FakeAnalyzer(FakeConnections())
        progress = []

        async def report(event):
            progress.append(event)

        result = await analyzer.analyze(
            snapshot, "admin-llm", [], progress=report
        )
        return result, progress

    result, progress = asyncio.run(run())
    assert result["source"]["import_mode"] == "llm"
    assert result["source"]["llm_connection_id"] == "admin-llm"
    assert [item.component_id for item in result["components"]] == [
        "reviewer",
        "review",
    ]
    reviewer = result["components"][0]
    review = result["components"][1]
    assert reviewer.content == "Use the review skill."
    assert reviewer.language == ""
    assert reviewer.dependencies == ["review"]
    assert reviewer.relations[0]["relation_type"] == "uses"
    assert review.variants == ["plugins/demo/skills/review/SKILL.md"]
    assert review.language == "lang_en"
    assert "lang_en" in review.labels
    assert result["errors"] == []
    assert [item["stage"] for item in progress] == [
        "llm_preparing",
        "llm_analyzing",
        "llm_chunk_complete",
        "validating",
    ]
    analyzing = progress[1]
    assert analyzing["paths"] == [
        "agents/reviewer.md",
        "hooks/install.js",
        "plugins/demo/skills/review/SKILL.md",
        "skills/review/SKILL.md",
    ]
    completed = progress[2]
    assert completed["chunk_components"] == 2
    assert completed["chunk_relations"] == 1
    assert completed["findings"] == [
        {
            "name": "Reviewer",
            "resource_type": "agent",
            "source_path": "agents/reviewer.md",
        },
        {
            "name": "Review",
            "resource_type": "skill",
            "source_path": "skills/review/SKILL.md",
        },
    ]


def test_llm_retries_when_first_response_has_no_json() -> None:
    class FakeConnections:
        async def get(self, _connection_id, _owner_id):
            return {
                "id": "admin-llm",
                "type": "openai",
                "model": "test-model",
                "is_active": True,
            }

    class RepairingAnalyzer(OfficialSourceLLMAnalyzer):
        calls = 0

        async def _invoke(self, _connection, prompt):
            self.calls += 1
            if self.calls == 1:
                assert "agents/reviewer.md" in prompt
                return "He revisado el repositorio, pero no devuelvo JSON."
            assert "<format_correction>" in prompt
            assert "agents/reviewer.md" in prompt
            return json.dumps(
                {
                    "schema_version": "1",
                    "components": [
                        {
                            "id": "reviewer",
                            "resource_type": "agent",
                            "name": "Reviewer",
                            "source_path": "agents/reviewer.md",
                        }
                    ],
                    "relations": [],
                    "warnings": [],
                }
            )

    async def run():
        analyzer = RepairingAnalyzer(FakeConnections())
        progress = []

        async def report(event):
            progress.append(event)

        result = await analyzer.analyze(
            {
                "source": {
                    "id": "draft",
                    "name": "Demo",
                    "repository_url": "https://github.com/example/demo",
                },
                "version": "v1",
                "commit_sha": "abc123",
                "files": {"agents/reviewer.md": "Review safely."},
            },
            "admin-llm",
            [],
            progress=report,
        )
        return analyzer.calls, result, progress

    calls, result, progress = asyncio.run(run())
    assert calls == 2
    assert [item.component_id for item in result["components"]] == ["reviewer"]
    assert "llm_retrying" in [item["stage"] for item in progress]


def test_llm_skips_one_invalid_chunk_and_keeps_partial_result(monkeypatch) -> None:
    from app.services import official_source_llm as llm_module

    monkeypatch.setattr(llm_module, "_CHUNK_FILES", 1)

    class FakeConnections:
        async def get(self, _connection_id, _owner_id):
            return {
                "id": "admin-llm",
                "type": "openai",
                "model": "test-model",
                "is_active": True,
            }

    class PartialAnalyzer(OfficialSourceLLMAnalyzer):
        async def _invoke(self, _connection, prompt):
            if "agents/reviewer.md" in prompt:
                return json.dumps(
                    {
                        "schema_version": "1",
                        "components": [
                            {
                                "id": "reviewer",
                                "resource_type": "agent",
                                "name": "Reviewer",
                                "source_path": "agents/reviewer.md",
                            }
                        ],
                        "relations": [],
                        "warnings": [],
                    }
                )
            return "Respuesta sin estructura"

    async def run():
        progress = []

        async def report(event):
            progress.append(event)

        result = await PartialAnalyzer(FakeConnections()).analyze(
            {
                "source": {
                    "id": "draft",
                    "name": "Demo",
                    "repository_url": "https://github.com/example/demo",
                },
                "version": "v1",
                "commit_sha": "abc123",
                "files": {
                    "agents/reviewer.md": "Review safely.",
                    "skills/broken/SKILL.md": "Broken response fixture.",
                },
            },
            "admin-llm",
            [],
            progress=report,
        )
        return result, progress

    result, progress = asyncio.run(run())
    assert [item.component_id for item in result["components"]] == ["reviewer"]
    assert "llm_chunk_failed" in [item["stage"] for item in progress]
    assert any("Fragmento 2/2 omitido" in item for item in result["security_warnings"])


def test_existing_draft_discards_legacy_programming_language_labels(
    admin_client,
) -> None:
    admin_id = next(
        user["id"]
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )

    async def run():
        storage = OfficialSourceStorage()
        component = PackageComponent(
            source_id="draft",
            component_id="compress",
            component_type="skill",
            name="Compress",
            source_path="skills/compress/SKILL.md",
            content="# Compress",
            content_hash="hash",
            labels=["official", "lang_python"],
            language="lang_python",
        )
        draft = await storage.create_draft(
            owner_id=admin_id,
            source={
                "name": "Legacy languages",
                "repository_url": "https://github.com/example/languages",
                "provider": "github",
                "repository_path": "example/languages",
                "repository_owner": "example",
                "repository_name": "languages",
                "tracking_mode": "branch",
                "tracking_ref": "main",
                "resolved_version": "v1",
                "commit_sha": "sha",
            },
            components=[component.as_dict(include_content=True)],
            errors=[
                "compress: etiquetas no válidas (lang_python)",
                "El grafo de dependencias contiene un ciclo",
            ],
        )
        return await storage.get_draft(draft["id"]), await storage.get_all_draft_components(
            draft["id"]
        )

    draft, components = asyncio.run(run())
    assert draft["errors"] == ["El grafo de dependencias contiene un ciclo"]
    assert components[0]["labels"] == ["official"]
    assert components[0]["language"] == ""
