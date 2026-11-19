"""The D156 tag tools: argument parsing and dispatch.

``list_tags`` reads the registry; ``create_tag``, ``update_tag``,
``delete_tag`` and ``update_document_tags`` write it. Every host parses the
arguments here and hands them to its own backend: the engine's in-process tag
port, or the typed HTTP SDK (which resolves a tag name to its id for the
routes that address one tag).
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel
from pydantic import ValidationError

from remember.mcp_tools._definitions import CREATE_TAG_TOOL_NAME
from remember.mcp_tools._definitions import DELETE_TAG_TOOL_NAME
from remember.mcp_tools._definitions import LIST_TAGS_TOOL_NAME
from remember.mcp_tools._definitions import TAG_TOOL_NAMES
from remember.mcp_tools._definitions import tool
from remember.mcp_tools._definitions import UPDATE_DOCUMENT_TAGS_TOOL_NAME
from remember.mcp_tools._definitions import UPDATE_TAG_TOOL_NAME
from remember.mcp_tools._errors import error_result
from remember.mcp_tools._errors import invalid_arguments
from remember.mcp_tools._errors import map_error
from remember.mcp_tools._errors import ToolArgumentError
from remember.mcp_tools._errors import ToolError
from remember.mcp_tools._memory import parse_tag_list
from remember.mcp_tools._memory import reject_unknown_keys
from remember.models import check_tag_ref
from remember.models import DocumentTags
from remember.models import DocumentTagsUpdate
from remember.models import Tag
from remember.models import TagCreate
from remember.models import TagList
from remember.models import TagUpdate

logger = logging.getLogger(__name__)


class TagBackend(Protocol):
    """Authority that answers the tag tools for one MCP composition."""

    def list_tags(self, *, query: str | None) -> TagList:
        """Every registered tag, optionally narrowed by a substring."""
        ...

    def create_tag(self, *, request: TagCreate) -> Tag:
        """Register one tag."""
        ...

    def update_tag(self, *, tag: str, request: TagUpdate) -> Tag:
        """Rename or re-describe the tag named by name or id."""
        ...

    def delete_tag(self, *, tag: str) -> Tag:
        """Delete the tag named by name or id, with its assignments."""
        ...

    def update_document_tags(
        self, *, doc_id: UUID, request: DocumentTagsUpdate
    ) -> DocumentTags:
        """Change one document's tags."""
        ...


def handle_tag_tool(
    *, name: str, arguments: Mapping[str, object], backend: TagBackend | None
) -> dict[str, object]:
    """Dispatch one tag tool to a success or structured error result."""
    if name not in TAG_TOOL_NAMES:
        raise ValueError(f"not a tag tool: {name!r}")
    if backend is None:
        return error_result(
            ToolError(
                code="tool_not_composed",
                detail=f"MCP tool {name!r} is not composed on this server.",
                status_code=None,
                retryable=False,
                agent_action="Tell the user tags are not available on this server.",
            )
        )
    try:
        result = _run(name=name, arguments=arguments, backend=backend)
    except ToolArgumentError as error:
        return error_result(error.error)
    except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
        if (
            getattr(error, "status_code", None) == 404
            and getattr(error, "detail", None) == "document_not_found"
        ):
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
        mapped = map_error(error)
        if mapped.code in {"internal_error", "local_backend_error"}:
            logger.exception("MCP tool %s failed with %s", name, mapped.code)
        return error_result(mapped)
    return {
        "content": [{"type": "text", "text": result.model_dump_json()}],
        "isError": False,
    }


def parse_tag_tool_arguments(
    *, name: str, arguments: Mapping[str, object]
) -> dict[str, object]:
    """Validate one tag tool's arguments and return them parsed."""
    properties = tool(name).input_schema["properties"]
    assert isinstance(properties, dict)
    reject_unknown_keys(arguments=arguments, allowed=set(properties))
    if name == LIST_TAGS_TOOL_NAME:
        query = arguments.get("query")
        if query is not None and (
            not isinstance(query, str) or not 1 <= len(query) <= 200
        ):
            raise _invalid(name=name, detail="query must be 1 to 200 characters.")
        return {"query": query}
    if name == CREATE_TAG_TOOL_NAME:
        return {"request": _model(name=name, model=TagCreate, payload=arguments)}
    if name == UPDATE_TAG_TOOL_NAME:
        return {
            "tag": _tag_ref(name=name, arguments=arguments),
            "request": _model(
                name=name,
                model=TagUpdate,
                payload={k: v for k, v in arguments.items() if k != "tag"},
            ),
        }
    if name == DELETE_TAG_TOOL_NAME:
        return {"tag": _tag_ref(name=name, arguments=arguments)}
    assert name == UPDATE_DOCUMENT_TAGS_TOOL_NAME
    raw_doc_id = arguments.get("doc_id")
    try:
        doc_id = UUID(str(raw_doc_id)) if isinstance(raw_doc_id, str) else None
    except ValueError:
        doc_id = None
    if doc_id is None:
        raise _invalid(name=name, detail="doc_id must be a document UUID.")
    payload = {
        key: parse_tag_list(arguments, key=key)
        for key in ("add", "remove", "set")
        if key in arguments
    }
    return {
        "doc_id": doc_id,
        "request": _model(name=name, model=DocumentTagsUpdate, payload=payload),
    }


def _run(
    *, name: str, arguments: Mapping[str, object], backend: TagBackend
) -> BaseModel:
    parsed = parse_tag_tool_arguments(name=name, arguments=arguments)
    if name == LIST_TAGS_TOOL_NAME:
        query = parsed["query"]
        assert query is None or isinstance(query, str)
        return backend.list_tags(query=query)
    if name == CREATE_TAG_TOOL_NAME:
        create = parsed["request"]
        assert isinstance(create, TagCreate)
        return backend.create_tag(request=create)
    if name == UPDATE_TAG_TOOL_NAME:
        update = parsed["request"]
        assert isinstance(update, TagUpdate)
        return backend.update_tag(tag=str(parsed["tag"]), request=update)
    if name == DELETE_TAG_TOOL_NAME:
        return backend.delete_tag(tag=str(parsed["tag"]))
    doc_id = parsed["doc_id"]
    request = parsed["request"]
    assert isinstance(doc_id, UUID) and isinstance(request, DocumentTagsUpdate)
    return backend.update_document_tags(doc_id=doc_id, request=request)


def _tag_ref(*, name: str, arguments: Mapping[str, object]) -> str:
    tag = arguments.get("tag")
    if not isinstance(tag, str):
        raise _invalid(name=name, detail="tag must be a tag name or tag_id.")
    try:
        return check_tag_ref(tag)
    except ValueError as error:
        raise _invalid(name=name, detail=str(error)) from error


def _model[M: BaseModel](
    *, name: str, model: type[M], payload: Mapping[str, object]
) -> M:
    try:
        return model.model_validate(dict(payload))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}:"
            f" {item['msg']}"
            for item in error.errors()
        )
        raise _invalid(name=name, detail=problems) from error


def _invalid(*, name: str, detail: str) -> ToolArgumentError:
    return ToolArgumentError(
        error=invalid_arguments(detail=f"Invalid {name} arguments: {detail}")
    )
