"""Shared, bounded context retrieval budgets for clients and the engine."""

from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

DEFAULT_CONTEXT_K: Final = 50
MAX_CONTEXT_K: Final = 100
MIN_CONTEXT_CANDIDATES: Final = 60
CONTEXT_OVERFETCH: Final = 4
DEFAULT_CONTEXT_CANDIDATES: Final = DEFAULT_CONTEXT_K * CONTEXT_OVERFETCH
MAX_CONTEXT_CANDIDATES: Final = MAX_CONTEXT_K * CONTEXT_OVERFETCH


class ContextRetrievalBudget(BaseModel):
    """Bound returned records and search work independently for each grain."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    k: int = Field(default=DEFAULT_CONTEXT_K, ge=1, le=MAX_CONTEXT_K)
    candidate_k: int | None = Field(default=None, ge=1, le=MAX_CONTEXT_CANDIDATES)

    @model_validator(mode="after")
    def _candidate_depth_covers_results(self) -> "ContextRetrievalBudget":
        """Reject an explicit pool smaller than the requested result count."""
        if self.candidate_k is not None and self.candidate_k < self.k:
            raise ValueError("candidate_k cannot be smaller than k")
        return self

    @property
    def candidate_depth(self) -> int:
        """Resolve omitted depth to fourfold overfetch with a small-pool floor."""
        if self.candidate_k is not None:
            return self.candidate_k
        return max(CONTEXT_OVERFETCH * self.k, MIN_CONTEXT_CANDIDATES)

    def resolved(self) -> "ContextRetrievalBudget":
        """Freeze the actual candidate depth for reproducible run configuration."""
        return ContextRetrievalBudget(k=self.k, candidate_k=self.candidate_depth)
