"""Provider adapter package."""

from typing import TYPE_CHECKING

from rememberstack.adapters.codex_writer import CodexAgentAdapterSettings
from rememberstack.adapters.codex_writer import CodexCLIAgentAdapter
from rememberstack.adapters.codex_writer import CodexCLIWriterAdapter
from rememberstack.adapters.codex_writer import CodexWriterAdapterSettings
from rememberstack.adapters.openrouter import OpenRouterModelProvider
from rememberstack.adapters.openrouter import OpenRouterProviderError
from rememberstack.adapters.openrouter import OpenRouterSettings
from rememberstack.adapters.postgres_p1 import PostgresP1Index

if TYPE_CHECKING:
    from rememberstack.adapters.markitdown_converter import MARKITDOWN_CONVERTER_VERSION
    from rememberstack.adapters.markitdown_converter import MarkitdownConverter

__all__ = (
    "CodexCLIAgentAdapter",
    "CodexCLIWriterAdapter",
    "CodexAgentAdapterSettings",
    "CodexWriterAdapterSettings",
    "MARKITDOWN_CONVERTER_VERSION",
    "MarkitdownConverter",
    "OpenRouterModelProvider",
    "OpenRouterProviderError",
    "OpenRouterSettings",
    "PostgresP1Index",
)


def __getattr__(name: str) -> object:
    """Load the media converter only for processes that actually compose it."""
    if name == "MARKITDOWN_CONVERTER_VERSION":
        from rememberstack.adapters.markitdown_converter import (
            MARKITDOWN_CONVERTER_VERSION,
        )

        return MARKITDOWN_CONVERTER_VERSION
    if name == "MarkitdownConverter":
        from rememberstack.adapters.markitdown_converter import MarkitdownConverter

        return MarkitdownConverter
    raise AttributeError(name)
