"""Dispatch proofs for the model-routed provider."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel
from pydantic import Field

from rememberstack.adapters import ModelRoutedProvider
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderCallUsage
from rememberstack.model import StructuredResponseModel
from rememberstack.ports import ModelProviderPort


class _Answer(BaseModel):
    """Minimal structured response for routing tests."""

    answer: Annotated[str, Field(min_length=1)]


class _Recording:
    """Provider that answers with its own name so dispatch is observable."""

    def __init__(self, *, name: str) -> None:
        self.name = name
        self.generated: list[str] = []
        self.embedded: list[str] = []

    def generate[ResponseT: StructuredResponseModel](
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        self.generated.append(request.model)
        return GeneratedResponse(
            output=response_type.model_validate({"answer": self.name}),
            usage=ProviderCallUsage(
                model_name=request.model,
                tokens_in=1,
                tokens_out=1,
                cost_usd=Decimal(0),
                latency_ms=1,
            ),
        )

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        self.embedded.append(request.model)
        return EmbeddingResponse(
            vectors=((0.0,),),
            usage=ProviderCallUsage(
                model_name=request.model,
                tokens_in=1,
                tokens_out=0,
                cost_usd=Decimal(0),
                latency_ms=1,
            ),
        )


def test_generate_and_embed_dispatch_on_the_pinned_model_name() -> None:
    """Listed models go to their adapter; everything else uses the default."""
    vertex = _Recording(name="vertex")
    openrouter = _Recording(name="openrouter")
    routed = ModelRoutedProvider(
        routes={"google/gemma-4-26b-a4b-it-maas": vertex}, default=openrouter
    )
    assert isinstance(routed, ModelProviderPort)

    gemma = routed.generate(
        request=ModelRequest(model="google/gemma-4-26b-a4b-it-maas", prompt="q"),
        response_type=_Answer,
    )
    luna = routed.generate(
        request=ModelRequest(model="openai/gpt-5.6-luna", prompt="q"),
        response_type=_Answer,
    )
    embedding = routed.embed(
        request=EmbeddingRequest(model="qwen/qwen3-embedding-8b", texts=("x",))
    )

    assert gemma.output.answer == "vertex"
    assert luna.output.answer == "openrouter"
    assert embedding.usage.model_name == "qwen/qwen3-embedding-8b"
    assert vertex.generated == ["google/gemma-4-26b-a4b-it-maas"]
    assert vertex.embedded == []
    assert openrouter.generated == ["openai/gpt-5.6-luna"]
    assert openrouter.embedded == ["qwen/qwen3-embedding-8b"]
    assert routed.provider_for(model="google/gemma-4-26b-a4b-it-maas") is vertex
    assert routed.provider_for(model="anything/else") is openrouter
