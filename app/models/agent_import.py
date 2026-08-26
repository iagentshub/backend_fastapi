"""Typed contracts for safe local agent and package imports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AgentImportResourceKind = Literal[
    "skill",
    "knowledge",
    "knowledge_pack",
    "prompt",
    "tool",
]
AgentImportReferenceStatus = Literal["matched", "ambiguous", "missing", "local"]
AgentImportComponentKind = Literal[
    "agent",
    "skill",
    "knowledge",
    "prompt",
    "tool",
]


class AgentImportIssue(BaseModel):
    """Stable warning code plus the field or values that caused it."""

    code: str
    field: str | None = None
    values: list[str] = Field(default_factory=list)


class AgentImportDraft(BaseModel):
    """Safe, editable subset that can be handed to the normal agent form."""

    name: str
    description: str = ""
    agent_type: Literal["generic", "claude", "openai", "github"] = "generic"
    model: str = ""
    system_prompt: str = ""
    temperature: float = 0.7
    scope: Literal["private"] = "private"
    labels: list[str] = Field(default_factory=lambda: ["private"])


class AgentImportCandidate(BaseModel):
    """Accessible existing resource that may satisfy one reference."""

    id: str
    name: str


class AgentImportReference(BaseModel):
    """One typed relationship requested by an imported agent."""

    key: str
    kind: AgentImportResourceKind
    source: str
    status: AgentImportReferenceStatus = "missing"
    selected_id: str | None = None
    local_component_id: str | None = None
    candidates: list[AgentImportCandidate] = Field(default_factory=list)


class AgentImportPreview(BaseModel):
    """Result of parsing one file; this contract never persists an agent."""

    filename: str
    source_format: Literal[
        "markdown",
        "claude_markdown",
        "github_markdown",
        "agent_json",
        "openai_json",
    ]
    draft: AgentImportDraft
    references: list[AgentImportReference] = Field(default_factory=list)
    issues: list[AgentImportIssue] = Field(default_factory=list)
    ignored_fields: list[str] = Field(default_factory=list)


class AgentDirectoryComponent(BaseModel):
    """Component discovered inside one selected local directory."""

    component_id: str
    kind: AgentImportComponentKind
    name: str
    description: str = ""
    source_path: str
    content_hash: str
    agent: AgentImportDraft | None = None
    references: list[AgentImportReference] = Field(default_factory=list)
    default_action: Literal["create", "reuse", "review", "skip"] = Field(
        default="create"
    )
    existing_candidates: list[AgentImportCandidate] = Field(default_factory=list)
    selected_existing_id: str | None = None
    security_blocked: bool = False


class AgentDirectoryImportPlan(BaseModel):
    """Non-persistent graph discovered from all compatible directory files."""

    components: list[AgentDirectoryComponent] = Field(default_factory=list)
    issues: list[AgentImportIssue] = Field(default_factory=list)
    ignored_paths: list[str] = Field(default_factory=list)

    @property
    def agent_count(self) -> int:
        return sum(component.kind == "agent" for component in self.components)


class AgentDirectoryComponentChoice(BaseModel):
    """User decision for a dependency component in a directory plan."""

    component_id: str
    action: Literal["create", "reuse", "skip"]
    resource_id: str | None = None


class AgentDirectoryReferenceChoice(BaseModel):
    """Manual resolution for an external or ambiguous agent relationship."""

    agent_component_id: str
    reference_key: str
    resource_id: str | None = None


class AgentDirectoryApplyOptions(BaseModel):
    """Selection confirmed by the user before transactional materialization."""

    selected_agent_ids: list[str] = Field(default_factory=list)
    component_choices: list[AgentDirectoryComponentChoice] = Field(default_factory=list)
    reference_choices: list[AgentDirectoryReferenceChoice] = Field(default_factory=list)


class AgentDirectoryImportedAgent(BaseModel):
    """Compact identity of one agent created by a directory import."""

    component_id: str
    resource_id: str
    name: str
    action: Literal["created"] = Field(default="created")


class AgentDirectoryImportedResource(BaseModel):
    """Compact identity and outcome of one materialized dependency."""

    component_id: str
    resource_type: AgentImportResourceKind
    resource_id: str
    action: Literal["created", "reused"]


class AgentDirectoryImportResult(BaseModel):
    """Resources created or reused while applying one package graph."""

    agents: list[AgentDirectoryImportedAgent] = Field(default_factory=list)
    resources: list[AgentDirectoryImportedResource] = Field(default_factory=list)
    agent_count: int = 0
    resource_count: int = 0
