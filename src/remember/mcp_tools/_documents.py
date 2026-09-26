"""The ``search_documents`` MCP tool (D134): argument parsing and dispatch.

Every host parses the flat tool arguments into one
:class:`~remember.models.DocumentSearchRequest` here and hands it to its own
backend: the engine's in-process search port, or the typed HTTP SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import logging
from typing import Protocol

from pydantic import ValidationError

from remember.mcp_tools._definitions import SEARCH_DOCUMENTS_TOOL_NAME
from remember.mcp_tools._errors import error_result
from remember.mcp_tools._errors import invalid_arguments
from remember.mcp_tools._errors import map_error
from remember.mcp_tools._errors import ToolArgumentError
from remember.mcp_tools._errors import ToolError
from remember.mcp_tools._memory import reject_unknown_keys
from remember.models import DocumentSearchPage
from remember.models import DocumentSearchRequest

logger = logging.getLogger(__name__)

_FILTER_KEYS = frozenset(
    {
        "family",
        "authors",
        "recipients",
        "created_from",
        "created_to",
        "modified_from",
        "modified_to",
        "language",
        "thread_ref",
        "doc_ids",
    }
)
_TOP_LEVEL_KEYS = frozenset({"query", "versions", "k", "cursor"})


class DocumentSearchBackend(Protocol):
    """Authority that answers ``search_documents`` for one MCP composition."""

    def search_documents(self, *, request: DocumentSearchRequest) -> DocumentSearchPage:
        """Run one document search."""
        ...


def parse_search_documents_arguments(
    *, arguments: Mapping[str, object]
) -> DocumentSearchRequest:
    """Validate the flat tool arguments into one search request."""
    reject_unknown_keys(
        arguments=arguments, allowed=set(_FILTER_KEYS | _TOP_LEVEL_KEYS)
    )
    payload: dict[str, object] = {
        key: value for key, value in arguments.items() if key in _TOP_LEVEL_KEYS
    }
    payload["filters"] = {
        key: value for key, value in arguments.items() if key in _FILTER_KEYS
    }
    try:
        return DocumentSearchRequest.model_validate(payload)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}:"
            f" {item['msg']}"
            for item in error.errors()
        )
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=f"Invalid search_documents arguments: {problems}"
            )
        ) from error


def handle_search_documents_tool(
    *, arguments: Mapping[str, object], backend: DocumentSearchBackend | None
) -> dict[str, object]:
    """Dispatch ``search_documents`` to a success or structured error result."""
    if backend is None:
        return error_result(
            ToolError(
                code="tool_not_composed",
                detail=(
                    f"MCP tool {SEARCH_DOCUMENTS_TOOL_NAME!r} is not composed on"
                    " this server (no document search port)."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Search content with the other tools instead; this server"
                    " cannot search documents by name or metadata."
                ),
            )
        )
    try:
        request = parse_search_documents_arguments(arguments=arguments)
        page = backend.search_documents(request=request)
    except ToolArgumentError as error:
        return error_result(error.error)
    except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
        if getattr(error, "status_code", None) == 400:
            refusal = invalid_arguments(
                detail=f"search_documents refused: {getattr(error, 'detail', error)}"
            )
            return error_result(replace(refusal, status_code=400))
        mapped = map_error(error)
        if mapped.code in {"internal_error", "local_backend_error"}:
            logger.exception(
                "MCP tool %s failed with %s", SEARCH_DOCUMENTS_TOOL_NAME, mapped.code
            )
        return error_result(mapped)
    return {
        "content": [{"type": "text", "text": page.model_dump_json()}],
        "isError": False,
    }
