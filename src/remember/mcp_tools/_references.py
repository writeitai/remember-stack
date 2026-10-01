"""The ``document_references`` MCP tool (D140 §6.2): argument parsing and dispatch.

Every host parses the tool arguments into one
:class:`~remember.models.DocumentReferencesRequest` here and hands it to its
own backend: the engine's in-process references port, or the typed HTTP SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import logging
from typing import Protocol

from pydantic import ValidationError

from remember.mcp_tools._definitions import DOCUMENT_REFERENCES_TOOL_NAME
from remember.mcp_tools._errors import error_result
from remember.mcp_tools._errors import invalid_arguments
from remember.mcp_tools._errors import map_error
from remember.mcp_tools._errors import ToolArgumentError
from remember.mcp_tools._errors import ToolError
from remember.mcp_tools._memory import reject_unknown_keys
from remember.models import DocumentReferencesPage
from remember.models import DocumentReferencesRequest

logger = logging.getLogger(__name__)

_KEYS = frozenset(
    {"chunk_id", "doc_id", "section_key", "direction", "kinds", "time", "k", "cursor"}
)


class DocumentReferencesBackend(Protocol):
    """Authority that answers ``document_references`` for one MCP composition."""

    def document_references(
        self, *, request: DocumentReferencesRequest
    ) -> DocumentReferencesPage:
        """Read one page of references."""
        ...


def parse_document_references_arguments(
    *, arguments: Mapping[str, object]
) -> DocumentReferencesRequest:
    """Validate the tool arguments into one references request."""
    reject_unknown_keys(arguments=arguments, allowed=set(_KEYS))
    try:
        return DocumentReferencesRequest.model_validate(dict(arguments))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}:"
            f" {item['msg']}"
            for item in error.errors()
        )
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=f"Invalid document_references arguments: {problems}"
            )
        ) from error


def handle_document_references_tool(
    *, arguments: Mapping[str, object], backend: DocumentReferencesBackend | None
) -> dict[str, object]:
    """Dispatch ``document_references`` to a success or structured error result."""
    if backend is None:
        return error_result(
            ToolError(
                code="tool_not_composed",
                detail=(
                    f"MCP tool {DOCUMENT_REFERENCES_TOOL_NAME!r} is not composed on"
                    " this server (no document references port)."
                ),
                status_code=None,
                retryable=False,
                agent_action="Read the documents with the other tools.",
            )
        )
    try:
        request = parse_document_references_arguments(arguments=arguments)
        page = backend.document_references(request=request)
    except ToolArgumentError as error:
        return error_result(error.error)
    except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
        status = getattr(error, "status_code", None)
        detail = getattr(error, "detail", None)
        if status == 404 and detail in {"document_not_found", "chunk_not_found"}:
            subject = (
                f"chunk {arguments.get('chunk_id')}"
                if detail == "chunk_not_found"
                else f"document {arguments.get('doc_id')}"
            )
            return error_result(
                ToolError(
                    code=str(detail),
                    detail=(
                        f"No live {subject}: the id is unknown or its document is"
                        " deleted."
                    ),
                    status_code=404,
                    retryable=False,
                    agent_action="Do not retry. Check the id.",
                )
            )
        if status == 400:
            refusal = invalid_arguments(
                detail=f"document_references refused: {detail or error}"
            )
            return error_result(replace(refusal, status_code=400))
        mapped = map_error(error)
        if mapped.code in {"internal_error", "local_backend_error"}:
            logger.exception(
                "MCP tool %s failed with %s", DOCUMENT_REFERENCES_TOOL_NAME, mapped.code
            )
        return error_result(mapped)
    return {
        "content": [{"type": "text", "text": page.model_dump_json()}],
        "isError": False,
    }
