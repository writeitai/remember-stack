"""The shared memory tool catalogue, `remember.mcp_tools` (D136).

One definition per memory tool, rendered identically by every MCP host, and
the source of the assured-operation registry's agent-facing fields.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from remember.mcp_tools import ADJACENT_CHUNKS_TOOL_NAME
from remember.mcp_tools import memory_tools
from remember.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools import OPERATION_TOOL_NAMES
from remember.mcp_tools import PROJECT_ARGUMENT
from remember.mcp_tools import render_tools_list
from remember.mcp_tools import tool
from remember.mcp_tools import ToolArgumentError
from remember.mcp_tools import validate_arguments
from rememberstack.model import DeploymentBuildInfo
from rememberstack.model.auth import PerimeterScope
from rememberstack.spine.assured_operations import CANONICAL_OPERATIONS
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.mcp import OperationMcpServer
from rememberstack.surfaces.operation_surface import operation_descriptors
from rememberstack.surfaces.route_scope import operation_scope
from rememberstack.surfaces.route_scope import required_scope

_DEPLOYMENT = UUID("57000000-0000-0000-0000-000000000009")


def _render(
    *, project: bool = False, path_ingest: bool = False, read_only: bool = False
) -> dict[str, dict[str, object]]:
    """Render the whole catalogue, keyed by tool name."""
    return {
        str(entry["name"]): entry
        for entry in render_tools_list(
            memory_tools(),
            project=project,
            path_ingest=path_ingest,
            read_only=read_only,
        )
    }


def _properties(entry: dict[str, object]) -> dict[str, object]:
    schema = entry["inputSchema"]
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    return properties


def test_catalogue_lists_exactly_the_memory_tools() -> None:
    """Write tools, the four operations and the seven query tools — no examples."""
    names = [definition.name for definition in memory_tools()]
    assert names == [
        "ingest",
        "pipeline_readiness",
        "delete_document",
        *OPERATION_TOOL_NAMES,
        ADJACENT_CHUNKS_TOOL_NAME,
        *OPEN_QUERY_TOOL_NAMES,
    ]
    assert len(set(names)) == len(names)
    assert not any(name.startswith("examples.") for name in names)
    for definition in memory_tools():
        assert definition.input_schema["additionalProperties"] is False
        assert definition.tool_version >= 1


def test_path_ingest_controls_the_local_path_body() -> None:
    """Only a host on the caller's machine offers `path`; the schema stays closed."""
    jsonschema = pytest.importorskip("jsonschema")
    with_path = _render(path_ingest=True)["ingest"]
    without_path = _render(path_ingest=False)["ingest"]
    assert "path" in _properties(with_path)
    assert "path" not in _properties(without_path)

    validator = jsonschema.Draft202012Validator(without_path["inputSchema"])
    assert not list(validator.iter_errors({"text": "hi", "filename": "a.md"}))
    assert not list(
        validator.iter_errors({"content_base64": "YQ==", "filename": "a.bin"})
    )
    assert list(validator.iter_errors({"path": "/tmp/x"}))
    assert list(
        validator.iter_errors(
            {"text": "hi", "content_base64": "YQ==", "filename": "a.md"}
        )
    )


def test_read_only_omits_every_write_tool() -> None:
    """`read_only` drops exactly the memory:write tools."""
    rendered = _render(read_only=True)
    assert "ingest" not in rendered
    assert "delete_document" not in rendered
    assert set(rendered) == {
        definition.name
        for definition in memory_tools()
        if definition.permission == "memory:read"
    }


def test_project_adds_the_routing_argument_with_the_catalogue_text() -> None:
    """A multi-deployment host adds `project`, optional, on every tool."""
    for name, entry in _render(project=True).items():
        assert _properties(entry)["project"] == PROJECT_ARGUMENT, name
        schema = entry["inputSchema"]
        assert isinstance(schema, dict)
        assert "project" not in schema.get("required", [])
    for entry in _render(project=False).values():
        assert "project" not in _properties(entry)


def test_rendering_never_shares_the_catalogue_schema() -> None:
    """A host editing its rendered copy cannot change another host's tools."""
    first = _render(project=True)
    _properties(first["facts_context"])["injected"] = {"type": "string"}
    assert "injected" not in _properties(_render()["facts_context"])
    assert "project" not in tool("facts_context").input_schema["properties"]  # type: ignore[operator]


def test_annotations_follow_permission() -> None:
    """Read tools are read-only; only delete_document is destructive."""
    for name, entry in _render().items():
        definition = tool(name)
        assert entry["annotations"] == {
            "readOnlyHint": definition.permission == "memory:read",
            "destructiveHint": name == "delete_document",
        }


def test_permission_matches_the_scope_the_engine_requires() -> None:
    """A tool's declared permission is what the perimeter demands of its route."""
    for definition in memory_tools():
        method, path = definition.http_route.split(" ", 1)
        concrete = (
            path.replace("{doc_id}", str(uuid4()))
            .replace("{chunk_id}", str(uuid4()))
            .replace("{namespace}", "examples")
            .replace("{name}", "top_entities")
        )
        scope = required_scope(method=method, path=concrete)
        if scope is None:
            scope = operation_scope(mutates=definition.mutates)
        if definition.permission == "memory:read":
            assert scope is PerimeterScope.READ, definition.name
        else:
            assert scope is not PerimeterScope.READ, definition.name


def test_operations_registry_fields_are_generated_from_the_catalogue() -> None:
    """`GET /operations` name/description/schema/mutates/version equal the catalogue."""
    descriptors = operation_descriptors(operations=CANONICAL_OPERATIONS)
    assert {descriptor.name for descriptor in descriptors} == set(OPERATION_TOOL_NAMES)
    for descriptor in descriptors:
        definition = tool(descriptor.name)
        assert descriptor.description == definition.description
        assert descriptor.input_schema == definition.input_schema
        assert descriptor.mutates is definition.mutates
        assert descriptor.version == definition.tool_version
    for operation in CANONICAL_OPERATIONS:
        definition = tool(operation.name.value)
        assert operation.description == definition.description
        assert operation.version == definition.tool_version


def test_validate_arguments_parses_every_family() -> None:
    """One entry point validates write, deletion, query, adjacent chunks, and operation calls."""
    doc_id = uuid4()
    assert validate_arguments("delete_document", {"doc_id": str(doc_id)}) == {
        "doc_id": doc_id
    }
    chunk_id = uuid4()
    assert validate_arguments(
        ADJACENT_CHUNKS_TOOL_NAME, {"chunk_id": str(chunk_id)}
    ) == {"chunk_id": chunk_id, "window": 1}
    assert validate_arguments(
        ADJACENT_CHUNKS_TOOL_NAME, {"chunk_id": str(chunk_id), "window": 2}
    ) == {"chunk_id": chunk_id, "window": 2}
    with pytest.raises(ToolArgumentError, match="Missing required arguments: chunk_id"):
        validate_arguments(ADJACENT_CHUNKS_TOOL_NAME, {})
    with pytest.raises(ToolArgumentError, match="chunk_id is not a valid UUID"):
        validate_arguments(ADJACENT_CHUNKS_TOOL_NAME, {"chunk_id": "not-a-uuid"})
    with pytest.raises(ToolArgumentError, match="window must be between 1 and 2"):
        validate_arguments(
            ADJACENT_CHUNKS_TOOL_NAME, {"chunk_id": str(chunk_id), "window": 3}
        )
    with pytest.raises(ToolArgumentError, match="Unknown argument keys: project"):
        validate_arguments(
            ADJACENT_CHUNKS_TOOL_NAME, {"chunk_id": str(chunk_id), "project": "p"}
        )
    ingest = validate_arguments("ingest", {"text": "hi", "filename": "a.md"})
    assert ingest["content"] == b"hi"
    assert ingest["mime"] == "text/markdown"
    assert validate_arguments("search_query_space", {"query": "people"}) == {
        "query": "people",
        "k": 10,
    }
    assert validate_arguments("resolve_entity", {"name": "Ada"}) == {"name": "Ada"}
    with pytest.raises(ToolArgumentError, match="Unknown argument keys: project"):
        validate_arguments("resolve_entity", {"name": "Ada", "project": "p"})
    with pytest.raises(ToolArgumentError, match="Missing required arguments: name"):
        validate_arguments("resolve_entity", {})
    with pytest.raises(ToolArgumentError, match="Unknown argument keys: path"):
        validate_arguments("ingest", {"path": "/tmp/x"}, path_ingest=False)
    with pytest.raises(ValueError, match="not a memory tool"):
        validate_arguments("examples.top_entities", {})


def test_engine_mcp_offers_no_path_and_refuses_it() -> None:
    """The engine does not run on the caller's machine, so `path` is unknown."""
    surface = MagicMock()
    surface.deployment_id = _DEPLOYMENT
    ingest = MagicMock()
    server = OperationMcpServer(
        surface=surface, ingest=ingest, pipeline_readiness=MagicMock()
    )
    tools = server.list_tools()["tools"]
    assert isinstance(tools, list)
    assert tools[0]["name"] == "ingest"
    assert "path" not in tools[0]["inputSchema"]["properties"]
    result = server.call_tool(name="ingest", arguments={"path": "/etc/hosts"})
    assert result["isError"] is True
    assert "Unknown argument keys: path" in str(result["content"])
    ingest.ingest.assert_not_called()


class _OpenBoundary:
    """Readiness and admission that let every request through."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[()]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


def _deployment_tools(**composed: object) -> dict[str, int]:
    """Build an API with the given optional ports and read `GET /deployment`."""
    boundary = _OpenBoundary()
    build_info = MagicMock()
    build_info.build_info.return_value = DeploymentBuildInfo(build_revision="abc")
    app = build_api(
        engine=MagicMock(),
        deployment_id=_DEPLOYMENT,
        admission=boundary,
        readiness=boundary,
        build_info=build_info,
        **composed,  # type: ignore[arg-type]
    )
    response = TestClient(app).get("/deployment")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["build_revision"] == "abc"
    return body["tools"]


def test_deployment_reports_exactly_the_composed_tools() -> None:
    """`tools` follows composition: query tools with open query, delete with deletion."""
    assert _deployment_tools() == {}

    surface = MagicMock()
    surface.deployment_id = _DEPLOYMENT
    open_query = MagicMock()
    open_query.deployment_id = _DEPLOYMENT
    everything = _deployment_tools(
        surface=surface,
        open_query=open_query,
        ingest=MagicMock(),
        pipeline_readiness=MagicMock(),
        deletion=MagicMock(),
    )
    assert everything == {
        definition.name: definition.tool_version for definition in memory_tools()
    }

    operations_only = _deployment_tools(surface=surface)
    assert set(operations_only) == {*OPERATION_TOOL_NAMES, ADJACENT_CHUNKS_TOOL_NAME}
    assert operations_only["facts_context"] == tool("facts_context").tool_version
    assert (
        operations_only[ADJACENT_CHUNKS_TOOL_NAME]
        == tool(ADJACENT_CHUNKS_TOOL_NAME).tool_version
    )


def test_read_only_mode_preserves_adjacent_chunks() -> None:
    """Read-only MCP mode preserves read tools including adjacent_chunks."""
    read_only_tools = _render(read_only=True)
    assert ADJACENT_CHUNKS_TOOL_NAME in read_only_tools
    assert "ingest" not in read_only_tools
    assert "delete_document" not in read_only_tools


def test_catalogue_imports_without_the_engine() -> None:
    """The base install can import it: no engine module loads with it."""
    probe = (
        "import sys, remember.mcp_tools\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'rememberstack')\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)
