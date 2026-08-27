"""Typed values and errors for immutable pipeline component versions."""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
import re
from typing import Annotated
from typing import Any
from typing import Self
from typing import TypeAlias
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from pydantic import StringConstraints

NonEmptyStrictString: TypeAlias = Annotated[
    str, StringConstraints(strict=True, min_length=1)
]


class PipelineComponent(StrEnum):
    """Exact values of the binding Postgres ``pipeline_component`` enum."""

    INGESTER = "ingester"
    CONVERTER = "converter"
    BLOCKIZER = "blockizer"
    STRUCTURER = "structurer"
    CROSSREFERENCER = "crossreferencer"
    CHUNKER = "chunker"
    CONTEXT_PREFIXER = "context_prefixer"
    EMBEDDING_INPUT_POLICY = "embedding_input_policy"
    EXTRACTOR = "extractor"
    GROUNDER = "grounder"
    RESOLVER = "resolver"
    NORMALIZER = "normalizer"
    ADJUDICATOR = "adjudicator"
    EMBEDDER = "embedder"
    FACT_LABELER = "fact_labeler"
    PROFILE_SUMMARIZER = "profile_summarizer"
    SNAPSHOT_BUILDER = "snapshot_builder"
    KNOWLEDGE_PLANNER = "knowledge_planner"
    KNOWLEDGE_WRITER = "knowledge_writer"
    KNOWLEDGE_REFLECTOR = "knowledge_reflector"
    KNOWLEDGE_LINTER = "knowledge_linter"
    JUDGE = "judge"
    FORGETTER = "forgetter"


class RegisterComponentVersionInput(BaseModel):
    """One explicit immutable pipeline component definition to register."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    component: PipelineComponent
    version: NonEmptyStrictString
    model_name: str | None = None
    prompt_hash: str | None = None
    embedding_dim: int | None = None
    params: Mapping[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("prompt_hash")
    @classmethod
    def require_lowercase_sha256(cls, value: str | None) -> str | None:
        """Require an exact lowercase hexadecimal SHA-256 when one is supplied."""
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(
                "prompt_hash must be exactly 64 lowercase hexadecimal characters"
            )
        return value

    @model_validator(mode="after")
    def require_embedder_for_embedding_dimension(self) -> Self:
        """Allow an embedding dimension only on the embedder component."""
        if (
            self.embedding_dim is not None
            and self.component is not PipelineComponent.EMBEDDER
        ):
            raise ValueError("embedding_dim is allowed only for the embedder component")
        return self


class RegisterComponentVersionResult(BaseModel):
    """Identity and creation disposition for one registration attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    component: PipelineComponent
    version: str
    created: bool


class ComponentVersionRecord(BaseModel):
    """A resolved immutable component definition including its database timestamp."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    component: PipelineComponent
    version: str
    model_name: str | None
    prompt_hash: str | None
    embedding_dim: int | None
    params: Mapping[str, Any]
    notes: str | None
    configured_at: datetime


class ComponentVersionError(RuntimeError):
    """Base class for a known component-version catalog failure."""


class ComponentVersionConflictError(ComponentVersionError):
    """An existing key has a different immutable component definition."""


class ComponentVersionNotFoundError(ComponentVersionError):
    """No component version exists for the requested primary-key triple."""
