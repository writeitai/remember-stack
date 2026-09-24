"""Agent-facing surfaces, imported lazily to keep the client wheel light."""

from __future__ import annotations

from importlib import import_module
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rememberstack.model.client import ToolDescriptor as ToolDescriptor
    from rememberstack.surfaces.consumption_skill import (
        ConsumptionSkillSurface as ConsumptionSkillSurface,
    )
    from rememberstack.surfaces.graph_queries import GraphQueries as GraphQueries
    from rememberstack.surfaces.http_api import build_api as build_api
    from rememberstack.surfaces.mcp import OperationMcpServer as OperationMcpServer
    from rememberstack.surfaces.operation_executor import (
        OperationExecutionError as OperationExecutionError,
    )
    from rememberstack.surfaces.operation_executor import (
        OperationExecutor as OperationExecutor,
    )
    from rememberstack.surfaces.operation_surface import (
        InvalidArgumentError as InvalidArgumentError,
    )
    from rememberstack.surfaces.operation_surface import (
        MissingArgumentError as MissingArgumentError,
    )
    from rememberstack.surfaces.operation_surface import (
        OperationSurface as OperationSurface,
    )
    from rememberstack.surfaces.operation_surface import (
        UnknownOperationError as UnknownOperationError,
    )
    from rememberstack.surfaces.query_engine import QueryEngine as QueryEngine
    from rememberstack.surfaces.query_sandbox.open_query import (
        OpenQueryFacade as OpenQueryFacade,
    )

_EXPORTS = {
    "ConsumptionSkillSurface": (
        "rememberstack.surfaces.consumption_skill",
        "ConsumptionSkillSurface",
    ),
    "GraphQueries": ("rememberstack.surfaces.graph_queries", "GraphQueries"),
    "InvalidArgumentError": (
        "rememberstack.surfaces.operation_surface",
        "InvalidArgumentError",
    ),
    "MissingArgumentError": (
        "rememberstack.surfaces.operation_surface",
        "MissingArgumentError",
    ),
    "OpenQueryFacade": (
        "rememberstack.surfaces.query_sandbox.open_query",
        "OpenQueryFacade",
    ),
    "QueryEngine": ("rememberstack.surfaces.query_engine", "QueryEngine"),
    "OperationExecutionError": (
        "rememberstack.surfaces.operation_executor",
        "OperationExecutionError",
    ),
    "OperationExecutor": (
        "rememberstack.surfaces.operation_executor",
        "OperationExecutor",
    ),
    "OperationMcpServer": ("rememberstack.surfaces.mcp", "OperationMcpServer"),
    "OperationSurface": (
        "rememberstack.surfaces.operation_surface",
        "OperationSurface",
    ),
    "ToolDescriptor": ("rememberstack.model.client", "ToolDescriptor"),
    "UnknownOperationError": (
        "rememberstack.surfaces.operation_surface",
        "UnknownOperationError",
    ),
    "build_api": ("rememberstack.surfaces.http_api", "build_api"),
}

__all__ = (
    "ConsumptionSkillSurface",
    "GraphQueries",
    "InvalidArgumentError",
    "MissingArgumentError",
    "OpenQueryFacade",
    "QueryEngine",
    "OperationExecutionError",
    "OperationExecutor",
    "OperationMcpServer",
    "OperationSurface",
    "ToolDescriptor",
    "UnknownOperationError",
    "build_api",
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
