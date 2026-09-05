"""Route model-provider calls to different adapters by requested model name.

The provider port (D61) bundles ``generate`` and ``embed`` because most
deployments buy both from one vendor. A benchmark that wants its answer agent
on one vendor while embeddings and the judge stay on another needs exactly one
thing: dispatch on the model identifier the caller already pins. This adapter
is that dispatch and nothing else; it owns no prompts, budgets, or accounting.
"""

from collections.abc import Mapping
from typing import TypeVar

from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ModelRequest
from rememberstack.model import StructuredResponseModel
from rememberstack.ports.model_provider import ModelProviderPort

ResponseT = TypeVar("ResponseT", bound=StructuredResponseModel)


class ModelRoutedProvider:
    """Send each call to the adapter bound to its model, else to the default."""

    def __init__(
        self, *, routes: Mapping[str, ModelProviderPort], default: ModelProviderPort
    ) -> None:
        """Bind exact model names to adapters; unlisted models use ``default``."""
        self._routes = dict(routes)
        self._default = default

    def provider_for(self, *, model: str) -> ModelProviderPort:
        """Return the adapter that will serve ``model``."""
        return self._routes.get(model, self._default)

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Delegate one structured generation by ``request.model``."""
        return self.provider_for(model=request.model).generate(
            request=request, response_type=response_type
        )

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Delegate one embedding batch by ``request.model``."""
        return self.provider_for(model=request.model).embed(request=request)
