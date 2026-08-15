"""Typed results for the WP-5.6 retrieval spike battery."""

from typing import Annotated
from typing import get_args
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

RetrievalSpikeName = Literal[
    "hub_pagination",
    "rerank_weights",
    "envelope_overhead",
    "hydration_batching",
    "resolve_context",
]

RETRIEVAL_SPIKE_NAMES = frozenset(get_args(RetrievalSpikeName))
"""The five retained WP-5.6 measurements; obsolete store benchmarking is removed."""


class RetrievalSpikeMeasurement(BaseModel):
    """One measured question, its selected setting, and honest limitations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: RetrievalSpikeName
    scale: Annotated[int, Field(ge=1)]
    metrics: dict[str, object]
    selected: dict[str, object]
    limitations: tuple[str, ...] = ()
    passed: bool


class RetrievalSpikeReport(BaseModel):
    """The complete five-spike WP-5.6 result written to ``eval_runs``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    measurements: Annotated[
        tuple[RetrievalSpikeMeasurement, ...], Field(min_length=5, max_length=5)
    ]

    @model_validator(mode="after")
    def complete_battery(self) -> "RetrievalSpikeReport":
        """Reject duplicate or missing measurements; absence is not compliance."""
        names = {measurement.name for measurement in self.measurements}
        if names != RETRIEVAL_SPIKE_NAMES:
            missing = sorted(RETRIEVAL_SPIKE_NAMES - names)
            extra = sorted(names - RETRIEVAL_SPIKE_NAMES)
            raise ValueError(
                f"retrieval spike names mismatch: missing={missing}, extra={extra}"
            )
        return self

    @property
    def passed(self) -> bool:
        """The battery passes only when every measured invariant holds."""
        return all(measurement.passed for measurement in self.measurements)
