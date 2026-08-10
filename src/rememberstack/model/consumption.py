"""Typed values for the deployment-rendered D51 consumption skill."""

from typing import Annotated
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.assured_operations import AssuredAnswerIntent
from rememberstack.model.envelope import Grain
from rememberstack.model.mounts import PublishedMounts

_NonEmptyText = Annotated[str, Field(min_length=1)]


class ConsumptionScope(BaseModel):
    """One deployment scope the rendered skill can name to a cold agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: _NonEmptyText
    name: _NonEmptyText
    description: str | None = None
    git_path: str | None = None


class ConsumptionOperation(BaseModel):
    """One active assured operation advertised by the rendered skill."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: _NonEmptyText
    description: _NonEmptyText
    output_grain: Grain | None
    answer_intent: AssuredAnswerIntent


class ConsumptionDeployment(BaseModel):
    """Deployment-owned data that varies between rendered skill revisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    slug: _NonEmptyText
    name: _NonEmptyText
    description: str | None = None
    default_language: _NonEmptyText
    scopes: tuple[ConsumptionScope, ...] = ()
    knowledge_page_count: int = Field(ge=0)


class ConsumptionSkillContext(BaseModel):
    """Complete typed input to the pure consumption-skill renderer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment: ConsumptionDeployment
    operations: tuple[ConsumptionOperation, ...]
    mounts: PublishedMounts | None = None


class RenderedConsumptionSkill(BaseModel):
    """One versioned, deployment-specific ``SKILL.md`` artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    version: _NonEmptyText
    filename: Literal["SKILL.md"] = "SKILL.md"
    content: _NonEmptyText


class S58Answer(BaseModel):
    """A cold harness's structured choices for the S58 consumption protocol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orientation: Literal["knowledge", "facts", "claims_as_of"]
    empty_knowledge: Literal["fallback_p3_or_search", "stop", "invent_summary"]
    current_truth: Literal["fact_lookup", "claim_search", "claims_as_of"]
    grain_handling: Literal["separate", "blend", "drop_evidence_label"]
    withdrawn_support: Literal["caveat_and_transcript", "trust", "discard"]
    claims_as_of: Literal["assertion_history_only", "current_truth", "compiled_truth"]
    contradictions: Literal["report_co_members", "pick_one", "ignore"]
    readable_content: Literal["prefer_mounts", "prefer_api", "prefer_raw"]
    audit: Literal["hydrate_to_sources", "read_k_only", "use_claims_as_fact"]
