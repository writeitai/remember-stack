"""Fail-closed proofs for ``remember mcp --read-only``."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
import json

import httpx
import pytest

from remember.cli import _build_parser
from remember.client import MemoryApiError
from remember.client import MemoryClient
from remember.mcp_memory_tools import MEMORY_WRITE_TOOL_NAMES
from remember.query_sandbox.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.remote_mcp import RemoteMcpServerConfig
from remember.remote_mcp import RemoteOperationMcpServer
from remember.remote_mcp import serve_mcp_stdio

_OPERATION = {
    "name": "resolve_entity",
    "description": "Resolve an entity.",
    "input_schema": {"type": "object"},
    "result_schema": {"type": "object"},
    "result_contract": "envelope",
    "output_grain": "fact",
    "answer_intent": "identity",
}
_VALID_IDENTITY = {
    "schema": "memory_v1",
    "schema_major": 1,
    "surface_manifest_hash": "ab" * 32,
}


def _client(respond: Callable[[httpx.Request], httpx.Response]) -> MemoryClient:
    transport = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    return MemoryClient(client=transport)


def _full_respond(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/operations":
        return httpx.Response(200, json=[_OPERATION])
    if request.url.path == "/query/space":
        return httpx.Response(200, json=_VALID_IDENTITY)
    return httpx.Response(404, json={"detail": "Not Found"})


def test_default_remote_mcp_still_lists_write_tools_first() -> None:
    """Default constructor remains the full catalog in stable order."""
    server = RemoteOperationMcpServer(client=_client(_full_respond))
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names[:2] == ["ingest", "pipeline_readiness"]
    assert "resolve_entity" in names
    assert names[-len(OPEN_QUERY_TOOL_NAMES) :] == list(OPEN_QUERY_TOOL_NAMES)


def test_read_only_filters_assured_operations_that_collide_with_write_names() -> None:
    """tools/list must drop reserved write names even if GET /operations returns them."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/operations":
            return httpx.Response(
                200,
                json=[
                    {**_OPERATION, "name": "ingest", "description": "colliding alias"},
                    {**_OPERATION, "name": "pipeline_readiness"},
                    _OPERATION,
                ],
            )
        if request.url.path == "/query/space":
            return httpx.Response(200, json=_VALID_IDENTITY)
        return httpx.Response(404, json={"detail": "Not Found"})

    server = RemoteOperationMcpServer(
        client=_client(respond), config=RemoteMcpServerConfig.read_only()
    )
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert "ingest" not in names
    assert "pipeline_readiness" not in names
    assert "resolve_entity" in names
    for name in MEMORY_WRITE_TOOL_NAMES:
        result = server.call_tool(
            name=name, arguments={"text": "x", "filename": "x.md"}
        )
        assert result["isError"] is True
        payload = json.loads(result["content"][0]["text"])  # type: ignore[index]
        assert payload["code"] == "write_tool_disabled"


def test_read_only_omits_write_tools_and_keeps_assured_and_open_query() -> None:
    """Read-only catalog is operations then open-query, never ingest."""
    server = RemoteOperationMcpServer(
        client=_client(_full_respond), config=RemoteMcpServerConfig.read_only()
    )
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names[0] == "resolve_entity"
    assert "ingest" not in names
    assert "pipeline_readiness" not in names
    assert names[-len(OPEN_QUERY_TOOL_NAMES) :] == list(OPEN_QUERY_TOOL_NAMES)


def test_read_only_rejects_write_tool_calls_without_falling_through() -> None:
    """Hidden write names never dispatch ingest or an assured alias."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/operations":
            return httpx.Response(
                200,
                json=[
                    {
                        **_OPERATION,
                        "name": "ingest",
                        "description": "Would be an assured alias.",
                    }
                ],
            )
        if request.url.path == "/query/space":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path.startswith("/operations/"):
            pytest.fail("write-tool call must not fall through to run_operation")
        if request.url.path == "/ingest":
            pytest.fail("write-tool call must not ingest")
        return httpx.Response(404, json={"detail": "Not Found"})

    server = RemoteOperationMcpServer(
        client=_client(respond), config=RemoteMcpServerConfig.read_only()
    )
    for name in MEMORY_WRITE_TOOL_NAMES:
        result = server.call_tool(
            name=name, arguments={"text": "x", "filename": "x.md"}
        )
        assert result["isError"] is True
        payload = json.loads(result["content"][0]["text"])  # type: ignore[index]
        assert payload["code"] == "write_tool_disabled"


def test_read_only_fails_closed_on_query_space_auth_error() -> None:
    """Auth failures must not look like an empty open-query catalog."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/operations":
            return httpx.Response(200, json=[_OPERATION])
        if request.url.path == "/query/space":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        return httpx.Response(404, json={"detail": "Not Found"})

    server = RemoteOperationMcpServer(
        client=_client(respond), config=RemoteMcpServerConfig.read_only()
    )
    with pytest.raises(MemoryApiError, match="API 401"):
        server.list_tools()


def test_read_only_fails_closed_on_query_space_transport_error() -> None:
    """A dead origin during read-only discovery is not an empty catalog."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/operations":
            return httpx.Response(200, json=[_OPERATION])
        if request.url.path == "/query/space":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(404, json={"detail": "Not Found"})

    server = RemoteOperationMcpServer(
        client=_client(respond), config=RemoteMcpServerConfig.read_only()
    )
    with pytest.raises(MemoryApiError, match="API 0"):
        server.list_tools()


def test_default_catalog_still_omits_open_query_on_query_space_error() -> None:
    """Full mode keeps the historical fail-open-omit for untrustworthy discovery."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/operations":
            return httpx.Response(200, json=[_OPERATION])
        if request.url.path == "/query/space":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        return httpx.Response(404, json={"detail": "Not Found"})

    server = RemoteOperationMcpServer(client=_client(respond))
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names == ["ingest", "pipeline_readiness", "resolve_entity"]


def test_read_only_stdio_initialize_and_tools_list() -> None:
    """Stdio protocol is unchanged aside from the omitted write tools."""
    server = RemoteOperationMcpServer(
        client=_client(_full_respond), config=RemoteMcpServerConfig.read_only()
    )
    requests = StringIO(
        "\n".join(
            json.dumps(value)
            for value in (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
        )
    )
    output = StringIO()
    assert (
        serve_mcp_stdio(server=server, input_stream=requests, output_stream=output) == 0
    )
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    names = [tool["name"] for tool in responses[1]["result"]["tools"]]
    assert "ingest" not in names
    assert names[0] == "resolve_entity"


def test_cli_exposes_read_only_flag_without_changing_default() -> None:
    """``remember mcp --read-only`` is opt-in."""
    parser = _build_parser()
    default = parser.parse_args(["mcp"])
    assert default.read_only is False
    flagged = parser.parse_args(["mcp", "--read-only"])
    assert flagged.read_only is True


def test_read_only_config_is_a_constructor_value() -> None:
    """Mode is selected by typed config, not an environment variable."""
    server = RemoteOperationMcpServer(
        client=_client(_full_respond), config=RemoteMcpServerConfig.read_only()
    )
    assert server.config.mode == "read_only"
