"""A deterministic in-memory model provider for behavior tests (no network)."""

from decimal import Decimal
import hashlib

from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderCallUsage
from rememberstack.model import StructuredResponseModel

_EMBEDDING_DIMENSION = 8


class FakeModelProvider:
    """Deterministic embeddings and canned structured generations for proofs."""

    def __init__(
        self,
        *,
        generate_payload: dict[str, object] | None = None,
        generate_payloads: dict[str, dict[str, object]] | None = None,
        generate_router: object | None = None,
    ) -> None:
        """Bind canned payloads: one default, one per response-type name, or a
        router callable (prompt, type_name) -> payload for per-call behavior."""
        self._generate_payload = generate_payload
        self._generate_payloads = generate_payloads or {}
        self._generate_router = generate_router
        self.embedded_texts: list[str] = []
        self.generated_prompts: list[str] = []
        self.generated_requests: list[ModelRequest] = []

    def generate[ResponseT: StructuredResponseModel](
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Return the canned payload validated as the caller's declared type."""
        self.generated_requests.append(request)
        self.generated_prompts.append(request.prompt)
        if callable(self._generate_router):
            output = response_type.model_validate(
                self._generate_router(request.prompt, response_type.__name__)
            )
        else:
            payload = self._generate_payloads.get(
                response_type.__name__, self._generate_payload
            )
            if payload is None:
                raise AssertionError(f"no canned payload for {response_type.__name__}")
            output = response_type.model_validate(payload)
        return GeneratedResponse(
            output=output,
            usage=_fake_usage(
                model_name=request.model, tokens_in=len(request.prompt.split())
            ),
        )

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return one deterministic content-derived vector per input text."""
        self.embedded_texts.extend(request.texts)
        return EmbeddingResponse(
            vectors=tuple(
                _vector_for(
                    text=text, dimensions=request.dimensions or _EMBEDDING_DIMENSION
                )
                for text in request.texts
            ),
            usage=_fake_usage(
                model_name=request.model,
                tokens_in=sum(len(text.split()) for text in request.texts),
            ),
        )


def _fake_usage(*, model_name: str, tokens_in: int) -> ProviderCallUsage:
    """Return deterministic zero-cost accounting for one in-memory provider call."""
    return ProviderCallUsage(
        model_name=model_name,
        tokens_in=tokens_in,
        tokens_out=0,
        cost_usd=Decimal(0),
        latency_ms=0,
    )


def _vector_for(*, text: str, dimensions: int) -> tuple[float, ...]:
    """Derive a stable pseudo-vector from the text content."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return tuple(digest[index % len(digest)] / 255.0 for index in range(dimensions))
