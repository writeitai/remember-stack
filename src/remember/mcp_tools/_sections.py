"""The ``section_history`` MCP tool (D140 §6.2): argument parsing and dispatch.

Every host parses the tool arguments into one
:class:`~remember.models.SectionHistoryRequest` here and hands it to its own
backend: the engine's in-process section-history port, or the typed HTTP SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import logging
from typing import Protocol

from pydantic import ValidationError

from remember.mcp_tools._definitions import SECTION_HISTORY_TOOL_NAME
from remember.mcp_tools._errors import error_result
from remember.mcp_tools._errors import invalid_arguments
from remember.mcp_tools._errors import map_error
from remember.mcp_tools._errors import ToolArgumentError
from remember.mcp_tools._errors import ToolError
from remember.mcp_tools._memory import reject_unknown_keys
from remember.models import SectionHistoryPage
from remember.models import SectionHistoryRequest

logger = logging.getLogger(__name__)

_KEYS = frozenset({"doc_id", "section_key", "time", "k", "cursor"})


class SectionHistoryBackend(Protocol):
    """Authority that answers ``section_history`` for one MCP composition."""

    def section_history(self, *, request: SectionHistoryRequest) -> SectionHistoryPage:
        """Read one page of a section's history."""
        ...


def parse_section_history_arguments(
    *, arguments: Mapping[str, object]
) -> SectionHistoryRequest:
    """Validate the tool arguments into one section-history request."""
    reject_unknown_keys(arguments=arguments, allowed=set(_KEYS))
    try:
        return SectionHistoryRequest.model_validate(dict(arguments))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}:"
            f" {item['msg']}"
            for item in error.errors()
        )
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=f"Invalid section_history arguments: {problems}"
            )
        ) from error


def handle_section_history_tool(
    *, arguments: Mapping[str, object], backend: SectionHistoryBackend | None
) -> dict[str, object]:
    """Dispatch ``section_history`` to a success or structured error result."""
    if backend is None:
        return error_result(
            ToolError(
                code="tool_not_composed",
                detail=(
                    f"MCP tool {SECTION_HISTORY_TOOL_NAME!r} is not composed on"
                    " this server (no section history port)."
                ),
                status_code=None,
                retryable=False,
                agent_action="Read the document's versions with the other tools.",
            )
        )
    try:
        request = parse_section_history_arguments(arguments=arguments)
        page = backend.section_history(request=request)
    except ToolArgumentError as error:
        return error_result(error.error)
    except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
        status = getattr(error, "status_code", None)
        detail = getattr(error, "detail", None)
        if status == 404 and detail == "document_not_found":
            return error_result(
                ToolError(
                    code="document_not_found",
                    detail=(
                        f"No live document {arguments.get('doc_id')}: the id is"
                        " unknown or the document is deleted."
                    ),
                    status_code=404,
                    retryable=False,
                    agent_action="Do not retry. Check the doc_id.",
                )
            )
        if status == 400:
            refusal = invalid_arguments(
                detail=f"section_history refused: {detail or error}"
            )
            return error_result(replace(refusal, status_code=400))
        mapped = map_error(error)
        if mapped.code in {"internal_error", "local_backend_error"}:
            logger.exception(
                "MCP tool %s failed with %s", SECTION_HISTORY_TOOL_NAME, mapped.code
            )
        return error_result(mapped)
    return {
        "content": [{"type": "text", "text": page.model_dump_json()}],
        "isError": False,
    }
