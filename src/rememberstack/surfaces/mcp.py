"""The MCP surface (retrieval §7, D50 + open query space §3.1).

Recipe tools still render from the recipe registry. When an `OpenQueryFacade`
is composed, the nine static infrastructure tools are listed and dispatched
alongside them. The seventeen `examples.*` identities are never top-level
tools — they run only through `run_saved_query`.
"""

from __future__ import annotations

import json

from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.mcp_tools import dispatch_open_query_tool
from rememberstack.surfaces.query_sandbox.mcp_tools import open_query_tool_descriptors
from rememberstack.surfaces.query_sandbox.mcp_tools import OPEN_QUERY_TOOL_NAMES
from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade
from rememberstack.surfaces.recipe_surface import InvalidArgumentError
from rememberstack.surfaces.recipe_surface import MissingArgumentError
from rememberstack.surfaces.recipe_surface import RecipeSurface
from rememberstack.surfaces.recipe_surface import UnknownRecipeError


class RecipeMcpServer:
    """Render and dispatch the recipe registry, plus optional open-query tools."""

    def __init__(
        self, *, surface: RecipeSurface, open_query: OpenQueryFacade | None = None
    ) -> None:
        """Bind the MCP server to the shared recipe surface and optional facade."""
        self._surface = surface
        self._open_query = open_query

    def list_tools(self) -> dict[str, object]:
        """The `tools/list` result: recipes plus static open-query infrastructure.

        Each recipe tool carries its name, description, and JSON-Schema
        `inputSchema` — the recipe registry rendered verbatim. When open query
        is composed, the nine §3.1 tools are appended. `examples.*` never
        appear as tools.
        """
        tools: list[dict[str, object]] = [
            {
                "name": descriptor.name,
                "description": descriptor.description,
                "inputSchema": descriptor.input_schema,
            }
            for descriptor in self._surface.descriptors()
        ]
        if self._open_query is not None:
            tools.extend(open_query_tool_descriptors())
        return {"tools": tools}

    def call_tool(
        self, *, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """The `tools/call` result: run a recipe or open-query infrastructure tool.

        Recipe answers are the D49 envelope as JSON text. Open-query answers
        are QueryResult/v1 or discovery payloads as JSON text. Unknown tools
        and typed failures are protocol error results (`isError`), never
        exceptions across the wire.
        """
        if name in OPEN_QUERY_TOOL_NAMES:
            if self._open_query is None:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"open-query tool {name!r} is not composed",
                        }
                    ],
                    "isError": True,
                }
            try:
                payload = dispatch_open_query_tool(
                    facade=self._open_query, name=name, arguments=arguments
                )
            except (SandboxRejection, ValueError, TypeError, KeyError) as error:
                return {
                    "content": [{"type": "text", "text": str(error)}],
                    "isError": True,
                }
            return {
                "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
                "isError": False,
            }
        try:
            envelope = self._surface.run(name=name, arguments=arguments)
        except (
            UnknownRecipeError,
            MissingArgumentError,
            InvalidArgumentError,
        ) as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}
        return {
            "content": [{"type": "text", "text": envelope.model_dump_json()}],
            "isError": False,
        }
