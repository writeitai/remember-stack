"""Typed, provider-neutral model and embedding call values for the LLM boundary."""

from decimal import Decimal
from typing import Annotated
from typing import Generic
from typing import Literal
from typing import Self
from typing import TypeAlias
from typing import TypeVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from rememberstack.model.processing import NonRetryableHandlerError

_NonEmptyText = Annotated[str, Field(min_length=1)]
_EmbeddingVector = Annotated[tuple[float, ...], Field(min_length=1)]
StructuredResponseModel: TypeAlias = BaseModel
ResponseT = TypeVar("ResponseT", bound=StructuredResponseModel)
ReasoningEffort: TypeAlias = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]


class ProviderAccountingError(NonRetryableHandlerError):
    """A paid response lacks accounting and must not be generated again automatically."""


class ProviderCallUsage(BaseModel):
    """Provider-reported accounting for one successful generation or embedding call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_name: _NonEmptyText
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=Decimal(0))
    latency_ms: int = Field(ge=0)


class ProviderCallError(Exception):
    """A provider call failed after it may already have reported billable usage."""

    def __init__(self, message: str, *, usage: ProviderCallUsage | None = None) -> None:
        """Keep parsed usage available to the worker's authoritative meter."""
        super().__init__(message)
        self.usage = usage


class ProviderInvalidResponseError(ProviderCallError):
    """A completed generation returned no value valid for the response schema."""


class GeneratedResponse(BaseModel, Generic[ResponseT]):
    """A validated structured output paired with its provider accounting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output: ResponseT
    usage: ProviderCallUsage


class ModelRequest(BaseModel):
    """A rendered prompt and configured model identifier ready for invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: _NonEmptyText
    prompt: _NonEmptyText
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = None


class EmbeddingRequest(BaseModel):
    """One non-empty batch for a configured embedding model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: _NonEmptyText
    texts: Annotated[tuple[_NonEmptyText, ...], Field(min_length=1)]


class EmbeddingResponse(BaseModel):
    """A non-empty embedding batch whose vectors share one dimension."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vectors: Annotated[tuple[_EmbeddingVector, ...], Field(min_length=1)]
    usage: ProviderCallUsage

    @model_validator(mode="after")
    def require_one_dimension(self) -> Self:
        """Reject provider responses that mix embedding dimensions."""
        expected_dimension = len(self.vectors[0])
        if any(len(vector) != expected_dimension for vector in self.vectors[1:]):
            raise ValueError("all embedding vectors must have the same dimension")
        return self
