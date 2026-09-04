"""Provider adapter package."""

from typing import TYPE_CHECKING

from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.codex_writer import CodexAgentAdapterSettings
from rememberstack.adapters.codex_writer import CodexCLIAgentAdapter
from rememberstack.adapters.codex_writer import CodexCLIWriterAdapter
from rememberstack.adapters.codex_writer import CodexWriterAdapterSettings
from rememberstack.adapters.openrouter import OpenRouterModelProvider
from rememberstack.adapters.openrouter import OpenRouterProviderError
from rememberstack.adapters.openrouter import OpenRouterSettings
from rememberstack.adapters.postgres_p1 import PostgresP1Index
from rememberstack.adapters.routed import ModelRoutedProvider
from rememberstack.adapters.vertex import VertexAccessError
from rememberstack.adapters.vertex import VertexModelProvider
from rememberstack.adapters.vertex import VertexProviderError
from rememberstack.adapters.vertex import VertexRequestError
from rememberstack.adapters.vertex import VertexSettings

if TYPE_CHECKING:
    from rememberstack.adapters.converters import build_conversion_routes
    from rememberstack.adapters.converters.markitdown import (
        MARKITDOWN_CONVERTER_VERSION,
    )
    from rememberstack.adapters.converters.markitdown import MarkitdownConverter
    from rememberstack.adapters.converters.mistral_ocr import MistralOcrConverter
    from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings

__all__ = (
    "BoundedPostgresReadPool",
    "CodexCLIAgentAdapter",
    "CodexCLIWriterAdapter",
    "CodexAgentAdapterSettings",
    "CodexWriterAdapterSettings",
    "MARKITDOWN_CONVERTER_VERSION",
    "MarkitdownConverter",
    "MistralOcrConverter",
    "MistralOcrSettings",
    "ModelRoutedProvider",
    "build_conversion_routes",
    "OpenRouterModelProvider",
    "OpenRouterProviderError",
    "OpenRouterSettings",
    "PostgresP1Index",
    "VertexAccessError",
    "VertexModelProvider",
    "VertexProviderError",
    "VertexRequestError",
    "VertexSettings",
)


def __getattr__(name: str) -> object:
    """Load the media converter only for processes that actually compose it."""
    if name == "MARKITDOWN_CONVERTER_VERSION":
        from rememberstack.adapters.converters.markitdown import (
            MARKITDOWN_CONVERTER_VERSION,
        )

        return MARKITDOWN_CONVERTER_VERSION
    if name == "MarkitdownConverter":
        from rememberstack.adapters.converters.markitdown import MarkitdownConverter

        return MarkitdownConverter
    if name == "build_conversion_routes":
        from rememberstack.adapters.converters import build_conversion_routes

        return build_conversion_routes
    if name == "MistralOcrConverter":
        from rememberstack.adapters.converters.mistral_ocr import MistralOcrConverter

        return MistralOcrConverter
    if name == "MistralOcrSettings":
        from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings

        return MistralOcrSettings
    raise AttributeError(name)
