"""Lo que el LLM tiene que devolver: manifiesto, componentes y relaciones."""


from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Literal

from pydantic import BaseModel, Field

LLMResourceType = Literal[
    "agent",
    "skill",
    "prompt",
    "knowledge",
    "tool",
    "memory",
    "workflow",
    "orchestrator",
    "ignore",
]

LLMRelationType = Literal["uses", "depends_on", "orchestrates", "contains"]

ProgressCallback = Callable[[Dict[str, Any]], Awaitable[None]]

class LLMManifestComponent(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    resource_type: LLMResourceType
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    source_path: str = Field(min_length=1, max_length=1_000)
    related_paths: List[str] = Field(default_factory=list, max_length=200)
    language: str = Field(default="", max_length=40)
    tool_language: str = Field(default="", max_length=40)
    labels: List[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(default="", max_length=1_000)

class LLMManifestRelation(BaseModel):
    from_id: str = Field(min_length=1, max_length=160)
    to_id: str = Field(min_length=1, max_length=160)
    relation_type: LLMRelationType
    evidence_path: str = Field(default="", max_length=1_000)
    evidence: str = Field(default="", max_length=500)

class LLMRepositoryManifest(BaseModel):
    schema_version: Literal["1"] = "1"
    components: List[LLMManifestComponent] = Field(default_factory=list)
    relations: List[LLMManifestRelation] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
