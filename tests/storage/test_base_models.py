"""Tests de la jerarquía BaseEntity/BaseResource y las dataclasses ligeras."""

from __future__ import annotations

from app.models import (
    Agent,
    BaseEntity,
    BaseResource,
    Connection,
    KnowledgeItem,
    Skill,
    Workflow,
)


def test_agent_inherits_base_resource():
    a = Agent(id="a1", name="Prueba")
    assert isinstance(a, BaseResource)
    assert isinstance(a, BaseEntity)
    assert a.is_active is True
    assert a.resource_type == "agent"


def test_agent_to_dict_includes_base_fields():
    d = Agent(id="a1", name="Prueba", owner_id="alice").to_dict()
    assert d["is_active"] is True
    assert d["resource_type"] == "agent"
    assert d["owner_id"] == "alice"


def test_agent_from_dict_reads_is_active():
    a = Agent.from_dict({"id": "a1", "name": "P", "is_active": False})
    assert a.is_active is False


def test_skill_roundtrip_preserves_unknown_keys():
    src = {"id": "s1", "name": "Skill", "content": "x", "campo_raro": 42}
    d = Skill.from_dict(src).to_dict()
    assert d["campo_raro"] == 42
    assert d["resource_type"] == "skill"
    assert d["is_active"] is True


def test_connection_roundtrip_preserves_credentials():
    src = {"id": "c1", "name": "Conn", "type": "openai", "api_key": "sk-xyz"}
    d = Connection.from_dict(src).to_dict()
    assert d["api_key"] == "sk-xyz"
    assert d["resource_type"] == "connection"


def test_knowledge_name_title_compat():
    item = KnowledgeItem.from_dict({"id": "k1", "title": "Doc antiguo"})
    assert item.name == "Doc antiguo"
    d = item.to_dict()
    assert d["name"] == "Doc antiguo"
    assert d["title"] == "Doc antiguo"


def test_workflow_definition_roundtrip():
    src = {"id": "w1", "name": "Flujo", "definition": {"nodes": [1], "edges": []}}
    d = Workflow.from_dict(src).to_dict()
    assert d["definition"] == {"nodes": [1], "edges": []}
    assert d["resource_type"] == "workflow"


def test_every_resource_type_is_registered():
    from app.models.resource_types import RESOURCE_TYPES

    for cls in (Agent, Skill, Connection, KnowledgeItem, Workflow):
        assert cls.resource_type in RESOURCE_TYPES
