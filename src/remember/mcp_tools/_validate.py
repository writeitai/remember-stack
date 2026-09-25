"""One entry point that validates any memory tool's arguments."""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
from typing import cast
from uuid import UUID

from remember.mcp_tools._definitions import ADJACENT_CHUNKS_TOOL_NAME
from remember.mcp_tools._definitions import DELETE_DOCUMENT_TOOL_NAME
from remember.mcp_tools._definitions import INGEST_TOOL_NAME
from remember.mcp_tools._definitions import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools._definitions import PIPELINE_READINESS_TOOL_NAME
from remember.mcp_tools._definitions import tool
from remember.mcp_tools._errors import invalid_arguments
from remember.mcp_tools._errors import ToolArgumentError
from remember.mcp_tools._memory import load_mcp_memory_settings
from remember.mcp_tools._memory import McpMemorySettings
from remember.mcp_tools._memory import parse_delete_document_arguments
from remember.mcp_tools._memory import parse_ingest_arguments
from remember.mcp_tools._memory import parse_pipeline_readiness_arguments
from remember.mcp_tools._memory import reject_unknown_keys
from remember.mcp_tools._query import validate_open_query_arguments
from remember.models import ADJACENT_CHUNKS_MAX_WINDOW
from remember.models import ADJACENT_CHUNKS_MIN_WINDOW


def parse_adjacent_chunks_arguments(
    arguments: Mapping[str, object],
) -> dict[str, object]:
    """Validate and parse adjacent_chunks arguments."""
    reject_unknown_keys(arguments=arguments, allowed={"chunk_id", "window"})
    if "chunk_id" not in arguments:
        raise ToolArgumentError(
            error=invalid_arguments(detail="Missing required arguments: chunk_id.")
        )
    raw_id = arguments["chunk_id"]
    if not isinstance(raw_id, (str, UUID)):
        raise ToolArgumentError(
            error=invalid_arguments(detail="chunk_id must be a UUID or string.")
        )
    try:
        chunk_uuid = UUID(str(raw_id))
    except ValueError as error:
        raise ToolArgumentError(
            error=invalid_arguments(detail=f"chunk_id is not a valid UUID: {raw_id!r}.")
        ) from error
    window = arguments.get("window", 1)
    if not isinstance(window, int) or isinstance(window, bool):
        raise ToolArgumentError(
            error=invalid_arguments(detail="window must be an integer (1 or 2).")
        )
    if window < ADJACENT_CHUNKS_MIN_WINDOW or window > ADJACENT_CHUNKS_MAX_WINDOW:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=(
                    f"window must be between {ADJACENT_CHUNKS_MIN_WINDOW} and"
                    f" {ADJACENT_CHUNKS_MAX_WINDOW}, got {window}."
                )
            )
        )
    return {"chunk_id": chunk_uuid, "window": window}


def validate_arguments(
    name: str,
    arguments: Mapping[str, object],
    *,
    path_ingest: bool = False,
    settings: McpMemorySettings | None = None,
    max_body_bytes: int | None = None,
) -> dict[str, object]:
    """Validate one call's arguments and return them parsed.

    ``ingest`` resolves its body to bytes (``content``) — reading a local
    ``path`` only when ``path_ingest`` is set and the operator configured
    allowlisted roots — and refuses an empty body or one over
    ``max_body_bytes``. ``pipeline_readiness`` returns ``version_ids`` and a
    ``require`` model; ``delete_document`` returns ``doc_id`` as a UUID.

    Raises :class:`ToolArgumentError` for the memory write tools and the
    assured operations, and ``SandboxRejection`` (the open-query error
    taxonomy) for the seven query tools. An unknown tool name is a
    ``ValueError``: a host lists and dispatches only catalogue tools.
    """
    try:
        definition = tool(name)
    except KeyError:
        raise ValueError(f"not a memory tool: {name!r}") from None
    if name == INGEST_TOOL_NAME:
        parsed = parse_ingest_arguments(
            arguments=arguments,
            path_ingest=path_ingest,
            settings=settings if settings is not None else load_mcp_memory_settings(),
            capability_limit=max_body_bytes,
        )
        return dataclasses.asdict(parsed)
    if name == PIPELINE_READINESS_TOOL_NAME:
        version_ids, require = parse_pipeline_readiness_arguments(arguments=arguments)
        return {"version_ids": version_ids, "require": require}
    if name == DELETE_DOCUMENT_TOOL_NAME:
        return {"doc_id": parse_delete_document_arguments(arguments=arguments)}
    if name in OPEN_QUERY_TOOL_NAMES:
        return validate_open_query_arguments(name=name, arguments=arguments)
    if name == ADJACENT_CHUNKS_TOOL_NAME:
        return parse_adjacent_chunks_arguments(arguments=arguments)
    # An assured operation: the engine validates values against its registry;
    # the catalogue enforces the closed argument object, so a stray key such as
    # a host's `project` routing argument is refused before it is sent.
    schema = definition.input_schema
    reject_unknown_keys(
        arguments=arguments,
        allowed=set(cast("dict[str, object]", schema["properties"])),
    )
    missing = sorted(
        set(cast("list[str]", schema.get("required", []))) - set(arguments)
    )
    if missing:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=f"Missing required arguments: {', '.join(missing)}."
            )
        )
    return dict(arguments)
