"""Agent-facing surfaces, imported lazily to keep the client wheel light."""

from __future__ import annotations

from importlib import import_module
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rememberstack.model.client import ToolDescriptor as ToolDescriptor
    from rememberstack.surfaces.cli import main as cli_main  # noqa: F401
    from rememberstack.surfaces.consumption_skill import (
        ConsumptionSkillSurface as ConsumptionSkillSurface,
    )
    from rememberstack.surfaces.graph_queries import GraphQueries as GraphQueries
    from rememberstack.surfaces.http_api import build_api as build_api
    from rememberstack.surfaces.mcp import RecipeMcpServer as RecipeMcpServer
    from rememberstack.surfaces.query_engine import QueryEngine as QueryEngine
    from rememberstack.surfaces.query_sandbox.open_query import (
        OpenQueryFacade as OpenQueryFacade,
    )
    from rememberstack.surfaces.recipe_executor import EXECUTABLE_OPS as EXECUTABLE_OPS
    from rememberstack.surfaces.recipe_executor import (
        RecipeExecutionError as RecipeExecutionError,
    )
    from rememberstack.surfaces.recipe_executor import RecipeExecutor as RecipeExecutor
    from rememberstack.surfaces.recipe_surface import (
        InvalidArgumentError as InvalidArgumentError,
    )
    from rememberstack.surfaces.recipe_surface import (
        MissingArgumentError as MissingArgumentError,
    )
    from rememberstack.surfaces.recipe_surface import RecipeSurface as RecipeSurface
    from rememberstack.surfaces.recipe_surface import (
        UnknownRecipeError as UnknownRecipeError,
    )
    from rememberstack.surfaces.remote_mcp import (
        RemoteRecipeMcpServer as RemoteRecipeMcpServer,
    )
    from rememberstack.surfaces.remote_mcp import serve_mcp_stdio as serve_mcp_stdio
    from rememberstack.surfaces.sdk import MemoryApiError as MemoryApiError
    from rememberstack.surfaces.sdk import MemoryClient as MemoryClient

_EXPORTS = {
    "EXECUTABLE_OPS": ("rememberstack.surfaces.recipe_executor", "EXECUTABLE_OPS"),
    "ConsumptionSkillSurface": (
        "rememberstack.surfaces.consumption_skill",
        "ConsumptionSkillSurface",
    ),
    "GraphQueries": ("rememberstack.surfaces.graph_queries", "GraphQueries"),
    "InvalidArgumentError": (
        "rememberstack.surfaces.recipe_surface",
        "InvalidArgumentError",
    ),
    "MemoryApiError": ("rememberstack.surfaces.sdk", "MemoryApiError"),
    "MemoryClient": ("rememberstack.surfaces.sdk", "MemoryClient"),
    "MissingArgumentError": (
        "rememberstack.surfaces.recipe_surface",
        "MissingArgumentError",
    ),
    "OpenQueryFacade": (
        "rememberstack.surfaces.query_sandbox.open_query",
        "OpenQueryFacade",
    ),
    "QueryEngine": ("rememberstack.surfaces.query_engine", "QueryEngine"),
    "RecipeExecutionError": (
        "rememberstack.surfaces.recipe_executor",
        "RecipeExecutionError",
    ),
    "RecipeExecutor": ("rememberstack.surfaces.recipe_executor", "RecipeExecutor"),
    "RecipeMcpServer": ("rememberstack.surfaces.mcp", "RecipeMcpServer"),
    "RecipeSurface": ("rememberstack.surfaces.recipe_surface", "RecipeSurface"),
    "RemoteRecipeMcpServer": (
        "rememberstack.surfaces.remote_mcp",
        "RemoteRecipeMcpServer",
    ),
    "ToolDescriptor": ("rememberstack.model.client", "ToolDescriptor"),
    "UnknownRecipeError": (
        "rememberstack.surfaces.recipe_surface",
        "UnknownRecipeError",
    ),
    "build_api": ("rememberstack.surfaces.http_api", "build_api"),
    "cli_main": ("rememberstack.surfaces.cli", "main"),
    "serve_mcp_stdio": ("rememberstack.surfaces.remote_mcp", "serve_mcp_stdio"),
}

__all__ = (
    "EXECUTABLE_OPS",
    "ConsumptionSkillSurface",
    "GraphQueries",
    "InvalidArgumentError",
    "MemoryApiError",
    "MemoryClient",
    "MissingArgumentError",
    "OpenQueryFacade",
    "QueryEngine",
    "RecipeExecutionError",
    "RecipeExecutor",
    "RecipeMcpServer",
    "RecipeSurface",
    "RemoteRecipeMcpServer",
    "ToolDescriptor",
    "UnknownRecipeError",
    "build_api",
    "cli_main",
    "serve_mcp_stdio",
)


def __getattr__(name: str) -> Any:
    """Load each surface only when callers request it."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
