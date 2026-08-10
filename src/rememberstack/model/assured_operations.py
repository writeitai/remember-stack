"""Closed assured-operation descriptors (D50/D87).

The four platform operations are registry data over existing zero-LLM query
authorities. Customer-defined retrieval belongs in ``saved_queries``; this
model deliberately cannot describe a fifth public operation.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from rememberstack.model.envelope import Grain


class AssuredOperationName(StrEnum):
    """The complete platform-owned assured-operation namespace."""

    RESOLVE_ENTITY = "resolve_entity"
    TESTIMONY_CONTEXT = "testimony_context"
    FACT_CONTEXT = "fact_context"
    ANSWER_CONTEXT = "answer_context"


class AssuredResultContract(StrEnum):
    """The closed wire contracts an assured operation may return."""

    ENVELOPE = "envelope"
    CONTEXT_BUNDLE_V1 = "context_bundle_v1"


class AssuredAnswerIntent(StrEnum):
    """The authority an operation is intended to answer from."""

    IDENTITY = "identity"
    TESTIMONY = "testimony"
    FACTS = "facts"
    COMBINED_CONTEXT = "combined_context"


class OperationStep(BaseModel):
    """One fixed operation invocation in a primitive-chain plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: Literal["resolve_entity", "testimony_context", "fact_context"]


class PrimitiveChainPlan(BaseModel):
    """A zero-LLM operation implemented by one or more fixed authorities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["primitive_chain"] = "primitive_chain"
    steps: tuple[OperationStep, ...] = Field(min_length=1)


class OperationBundlePlan(BaseModel):
    """The exact pure-composition plan used only by ``answer_context``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["operation_bundle"] = "operation_bundle"
    children: tuple[
        Literal["testimony_context"], Literal["fact_context"]
    ] = ("testimony_context", "fact_context")


ExecutionPlan = Annotated[
    PrimitiveChainPlan | OperationBundlePlan, Field(discriminator="kind")
]


class CurrentFactTime(BaseModel):
    """Select facts valid at the disclosed current evaluation instant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["current"] = "current"


class AtFactTime(BaseModel):
    """Select facts whose world-valid interval covers one instant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["at"] = "at"
    at: datetime


class OverlapFactTime(BaseModel):
    """Select facts whose world-valid intervals overlap inclusive bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    mode: Literal["overlap"] = "overlap"
    from_: datetime = Field(alias="from")
    to: datetime

    @model_validator(mode="after")
    def _ordered(self) -> "OverlapFactTime":
        """Reject a world-time window whose end precedes its start."""
        if self.to < self.from_:
            raise ValueError("time.to must be at or after time.from")
        return self


class HistoryFactTime(BaseModel):
    """Select all currently believed intervals that began by evaluation time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["history"] = "history"


FactTime = Annotated[
    CurrentFactTime | AtFactTime | OverlapFactTime | HistoryFactTime,
    Field(discriminator="mode"),
]


class AssuredOperation(BaseModel):
    """One canonical registry row and its complete public contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: AssuredOperationName
    description: str = Field(min_length=1)
    parameters: dict[str, object]
    result_schema: dict[str, object]
    execution_plan: ExecutionPlan
    result_contract: AssuredResultContract
    output_grain: Grain | None
    answer_intent: AssuredAnswerIntent
    version: int = Field(default=1, ge=1)
