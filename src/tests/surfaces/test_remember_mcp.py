"""`remember mcp` (D136 §5): engine mode over stdio and Streamable HTTP, and bridge mode.

Engine mode runs against a mocked engine HTTP API; the HTTP transport runs a
real listener on a loopback port in front of a small fake engine; bridge mode
runs against a fake remote MCP endpoint (the hosted remember.dev endpoint is
specified by the design contract, not yet deployed).
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from io import StringIO
import json
import socket
import threading
import time
from typing import cast
from uuid import uuid4

import httpx
import pytest

from remember.cli import main
from remember.client import MemoryClient
from remember.errors import RateLimited
from remember.issuer import IssuerMetadata
from remember.mcp_bridge import McpBridge
from remember.mcp_engine import EngineMcpServer
from remember.mcp_engine import serve_stdio
from remember.mcp_http import DEFAULT_BIND
from remember.mcp_http import HttpTransportError
from remember.mcp_http import MAX_BODY_BYTES
from remember.mcp_http import MAX_CONCURRENT_REQUESTS
from remember.mcp_http import McpHttpServer
from remember.mcp_http import parse_bind
from remember.mcp_tools import map_error
from remember.mcp_tools import memory_tools
from remember.mcp_tools import OPERATION_TOOL_NAMES
from remember.mcp_tools import tool
from rememberstack.surfaces.mcp import OperationMcpServer
from tests.surfaces.fake_issuer import ISSUER
from tests.surfaces.fake_issuer import make_key

_ENVELOPE = {
    "grain": "fact",
    "temporal_scope": {
        "mode": "current",
        "evaluated_at": "2026-07-20T10:30:00Z",
        "believed_at": "2026-07-20T10:30:00Z",
        "identity_regime": "current",
    },
    "freshness": {"pg_live_ts": "2026-07-20T10:30:00Z"},
}
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


def _served(**overrides: int | None) -> dict[str, int]:
    """Every catalogue tool at its version; ``None`` drops one, an int changes it."""
    served: dict[str, int] = {item.name: item.tool_version for item in memory_tools()}
    for name, version in overrides.items():
        if version is None:
            served.pop(name)
        else:
            served[name] = version
    return served


def _engine(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    served: dict[str, int] | None = None,
    seen: list[httpx.Request] | None = None,
) -> MemoryClient:
    """A client over a mocked engine: ``/deployment`` plus ``handler``."""

    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/deployment":
            return httpx.Response(
                200, json={"tools": _served() if served is None else served}
            )
        if handler is not None:
            return handler(request)
        return httpx.Response(200, json=_ENVELOPE)

    return MemoryClient(
        client=httpx.Client(
            base_url="http://engine.test", transport=httpx.MockTransport(respond)
        )
    )


def _error(result: dict[str, object]) -> dict[str, object]:
    assert result["isError"] is True
    content = cast("list[dict[str, str]]", result["content"])
    return json.loads(content[0]["text"])["error"]


def _stdio(server: EngineMcpServer, *messages: dict[str, object]) -> list[dict]:
    output = StringIO()
    lines = "\n".join(json.dumps(message) for message in messages)
    assert (
        serve_stdio(server=server, input_stream=StringIO(lines), output_stream=output)
        == 0
    )
    return [json.loads(line) for line in output.getvalue().splitlines()]


# ---------------------------------------------------------------------------
# Engine mode
# ---------------------------------------------------------------------------


def test_engine_mode_lifecycle_over_stdio() -> None:
    """initialize, tools/list from the catalogue, a call, and a silent notification."""
    server = EngineMcpServer(client=_engine(), path_ingest=True)
    responses = _stdio(
        server,
        _INITIALIZE,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "resolve_entity", "arguments": {"name": "Alice"}},
        },
    )
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"
    names = [entry["name"] for entry in responses[1]["result"]["tools"]]
    assert names == [item.name for item in memory_tools()]
    assert responses[2]["result"]["isError"] is False


def test_a_tool_is_listed_only_at_an_equal_served_version() -> None:
    """Missing or differently versioned tools are omitted; the rest keep order."""
    ingest = tool("ingest")
    served = _served(ingest=ingest.tool_version + 1, query_sql=None, facts_context=None)
    server = EngineMcpServer(client=_engine(served=served), path_ingest=True)
    names = [entry["name"] for entry in server.list_tools()["tools"]]  # type: ignore[index]
    assert "ingest" not in names
    assert "query_sql" not in names
    assert "facts_context" not in names
    assert "pipeline_readiness" in names


def test_engine_mode_renders_what_the_in_process_server_renders() -> None:
    """One catalogue: the same options give the same entries on both hosts."""

    class _Surface:
        deployment_id = uuid4()

    in_process = OperationMcpServer(surface=_Surface())  # type: ignore[arg-type]
    engine = EngineMcpServer(
        client=_engine(
            served={name: tool(name).tool_version for name in OPERATION_TOOL_NAMES}
        ),
        path_ingest=False,
    )
    assert engine.list_tools() == in_process.list_tools()


def test_engine_mode_offers_path_ingest_only_when_asked() -> None:
    stdio = EngineMcpServer(client=_engine(), path_ingest=True).list_tools()
    remote = EngineMcpServer(client=_engine(), path_ingest=False).list_tools()

    def ingest_properties(listed: dict[str, object]) -> dict[str, object]:
        entry = next(e for e in listed["tools"] if e["name"] == "ingest")  # type: ignore[union-attr, index]
        return entry["inputSchema"]["properties"]

    assert "path" in ingest_properties(stdio)
    assert "path" not in ingest_properties(remote)


def test_engine_mode_refuses_project_without_calling_the_engine() -> None:
    seen: list[httpx.Request] = []
    server = EngineMcpServer(client=_engine(seen=seen), path_ingest=True)
    error = _error(
        server.call_tool(
            name="resolve_entity", arguments={"name": "Alice", "project": "docs"}
        )
    )
    assert error["code"] == "project_routing_unavailable"
    assert error["status_code"] is None
    assert seen == []


def test_unknown_tool_is_refused_locally() -> None:
    seen: list[httpx.Request] = []
    server = EngineMcpServer(client=_engine(seen=seen), path_ingest=True)
    assert (
        _error(server.call_tool(name="broken", arguments={}))["code"] == "unknown_tool"
    )
    assert seen == []


def test_read_only_omits_and_refuses_every_write_tool() -> None:
    seen: list[httpx.Request] = []
    server = EngineMcpServer(
        client=_engine(seen=seen), read_only=True, path_ingest=True
    )
    names = [entry["name"] for entry in server.list_tools()["tools"]]  # type: ignore[index]
    assert "ingest" not in names and "delete_document" not in names
    assert "pipeline_readiness" in names
    for name, arguments in (
        ("ingest", {"text": "x", "filename": "x.md"}),
        ("delete_document", {"doc_id": "00000000-0000-0000-0000-000000000001"}),
    ):
        assert (
            _error(server.call_tool(name=name, arguments=dict(arguments)))["code"]
            == "read_only"
        )
    assert [request.url.path for request in seen] == ["/deployment"]


@pytest.mark.parametrize("failure", [401, 403, 503, "network"])
def test_tools_list_fails_loudly_when_the_deployment_cannot_be_read(
    failure: int | str,
) -> None:
    """A bad key or dead engine is a JSON-RPC error, never an empty tool list."""

    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "network":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(cast(int, failure), json={"detail": "no"})

    client = MemoryClient(
        client=httpx.Client(
            base_url="http://engine.test", transport=httpx.MockTransport(respond)
        )
    )
    responses = _stdio(
        EngineMcpServer(client=client, path_ingest=True),
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert responses[0]["error"]["code"] == -32603


@pytest.mark.parametrize("code", ["rate_limited", "concurrency_limited"])
def test_admission_refusal_is_a_tool_error_with_retry_after(code: str) -> None:
    """The engine's 429 keeps its code and Retry-After for the agent."""

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"detail": {"code": code, "message": "slow down"}},
        )

    server = EngineMcpServer(client=_engine(respond), path_ingest=True)
    for name, arguments in (
        ("facts_context", {"query": "who"}),
        ("query_sql", {"sql": "SELECT 1"}),
        ("ingest", {"text": "x", "filename": "x.md"}),
    ):
        error = _error(server.call_tool(name=name, arguments=dict(arguments)))
        assert error["code"] == code
        assert error["status_code"] == 429
        assert error["retryable"] is True
        assert error["retry_after"] == 7


def test_map_error_keeps_the_rate_limited_type_fields() -> None:
    mapped = map_error(
        RateLimited(detail="busy", code="concurrency_limited", retry_after=1.0)
    )
    assert (mapped.code, mapped.status_code, mapped.retry_after) == (
        "concurrency_limited",
        429,
        1.0,
    )


def test_every_tool_family_reports_the_one_envelope() -> None:
    """Writes, operations and query tools fail in the same shape."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/query/"):
            return httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "relation_not_allowed",
                        "message": "no such view",
                    }
                },
            )
        return httpx.Response(503, json={"detail": "database unavailable"})

    server = EngineMcpServer(client=_engine(respond), path_ingest=True)
    query = _error(server.call_tool(name="query_sql", arguments={"sql": "SELECT 1"}))
    operation = _error(server.call_tool(name="facts_context", arguments={"query": "q"}))
    write = _error(
        server.call_tool(name="ingest", arguments={"text": "x", "filename": "x.md"})
    )
    local = _error(server.call_tool(name="facts_context", arguments={}))
    for error in (query, operation, write, local):
        assert {"code", "status_code", "detail", "retryable", "agent_action"} <= set(
            error
        )
    assert (query["code"], query["status_code"]) == ("relation_not_allowed", 422)
    assert (operation["code"], operation["retryable"]) == ("engine_unavailable", True)
    assert write["status_code"] == 503
    assert (local["code"], local["status_code"]) == ("invalid_arguments", None)


def test_a_403_is_insufficient_permission() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"detail": "credential may not perform this operation"}
        )

    server = EngineMcpServer(client=_engine(respond), path_ingest=True)
    error = _error(server.call_tool(name="facts_context", arguments={"query": "q"}))
    assert error["code"] == "insufficient_permission"


# ---------------------------------------------------------------------------
# Streamable HTTP transport
# ---------------------------------------------------------------------------


class _FakeEngine(ThreadingHTTPServer):
    """A tiny engine: serves the catalogue and records each Authorization."""

    def __init__(self) -> None:
        self.authorizations: list[str | None] = []
        engine = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

            def _send(self, body: object) -> None:
                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802
                engine.authorizations.append(self.headers.get("Authorization"))
                self._send({"tools": _served()})

            def do_POST(self) -> None:  # noqa: N802
                engine.authorizations.append(self.headers.get("Authorization"))
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self._send(_ENVELOPE)

        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


@pytest.fixture()
def listener() -> Iterator[tuple[McpHttpServer, _FakeEngine]]:
    engine = _FakeEngine()
    server = McpHttpServer(
        host="127.0.0.1", port=0, engine_url=engine.url, read_only=False
    )
    threads = [
        threading.Thread(target=target.serve_forever, daemon=True)
        for target in (engine, server)
    ]
    for thread in threads:
        thread.start()
    try:
        yield server, engine
    finally:
        for target in (server, engine):
            target.shutdown()
            target.server_close()


def _post(
    server: McpHttpServer,
    message: dict[str, object],
    *,
    session: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    sent = {"Accept": "application/json, text/event-stream", **(headers or {})}
    if session is not None:
        sent["Mcp-Session-Id"] = session
    return httpx.post(server.url, json=message, headers=sent)


def test_http_sessions(listener: tuple[McpHttpServer, _FakeEngine]) -> None:
    server, _ = listener
    opened = _post(server, _INITIALIZE)
    assert opened.status_code == 200
    session = opened.headers["Mcp-Session-Id"]
    assert opened.json()["result"]["protocolVersion"] == "2025-11-25"

    ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    assert _post(server, ping, session=session).json()["result"] == {}
    assert _post(server, ping).status_code == 400
    assert _post(server, ping, session="unknown").status_code == 404
    notification: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    assert _post(server, notification, session=session).status_code == 202

    assert (
        httpx.delete(server.url, headers={"Mcp-Session-Id": session}).status_code == 204
    )
    assert _post(server, ping, session=session).status_code == 404


def test_http_get_is_405(listener: tuple[McpHttpServer, _FakeEngine]) -> None:
    server, _ = listener
    assert httpx.get(server.url).status_code == 405


def test_http_refuses_a_foreign_origin(
    listener: tuple[McpHttpServer, _FakeEngine],
) -> None:
    server, _ = listener
    foreign = _post(server, _INITIALIZE, headers={"Origin": "http://evil.test:8765"})
    assert foreign.status_code == 403
    port = server.server_address[1]
    for own in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        assert _post(server, _INITIALIZE, headers={"Origin": own}).status_code == 200


def test_http_forwards_each_callers_bearer_and_stores_none(
    listener: tuple[McpHttpServer, _FakeEngine],
) -> None:
    server, engine = listener
    session = _post(server, _INITIALIZE).headers["Mcp-Session-Id"]
    listed = _post(
        server,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        session=session,
        headers={"Authorization": "Bearer caller-one"},
    )
    assert len(listed.json()["result"]["tools"]) == len(memory_tools())
    names = [entry["name"] for entry in listed.json()["result"]["tools"]]
    ingest = next(e for e in listed.json()["result"]["tools"] if e["name"] == "ingest")
    assert "path" not in ingest["inputSchema"]["properties"]
    assert "ingest" in names
    _post(
        server,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "resolve_entity", "arguments": {"name": "A"}},
        },
        session=session,
    )
    assert engine.authorizations == ["Bearer caller-one", None]


def test_http_binds_loopback_by_default() -> None:
    host, _ = parse_bind(DEFAULT_BIND)
    assert host == "127.0.0.1"


@pytest.mark.parametrize("bind", ["0.0.0.0:8765", "192.168.1.5:8765", "[::]:8765"])
def test_http_binds_loopback_only(bind: str) -> None:
    with pytest.raises(HttpTransportError, match="loopback"):
        parse_bind(bind)
    assert parse_bind("[::1]:8765") == ("::1", 8765)


def test_http_opens_a_session_only_on_a_successful_initialize(
    listener: tuple[McpHttpServer, _FakeEngine],
) -> None:
    server, _ = listener
    bad = _post(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert "error" in bad.json()
    assert "Mcp-Session-Id" not in bad.headers


def test_http_refuses_an_unsupported_protocol_version(
    listener: tuple[McpHttpServer, _FakeEngine],
) -> None:
    server, _ = listener
    session = _post(server, _INITIALIZE).headers["Mcp-Session-Id"]
    ping: dict[str, object] = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    old = _post(
        server, ping, session=session, headers={"MCP-Protocol-Version": "2024-11-05"}
    )
    assert old.status_code == 400
    current = _post(
        server, ping, session=session, headers={"MCP-Protocol-Version": "2025-11-25"}
    )
    assert current.status_code == 200


def test_http_refuses_an_oversized_body(
    listener: tuple[McpHttpServer, _FakeEngine],
) -> None:
    server, _ = listener
    with socket.create_connection(("127.0.0.1", server.server_address[1])) as raw:
        raw.sendall(
            b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: "
            + str(MAX_BODY_BYTES + 1).encode()
            + b"\r\n\r\n"
        )
        assert raw.recv(64).startswith(b"HTTP/1.1 413")


def test_http_bounds_concurrent_requests(
    listener: tuple[McpHttpServer, _FakeEngine],
) -> None:
    server, _ = listener
    for _ in range(MAX_CONCURRENT_REQUESTS):
        assert server.slots.acquire(blocking=False)
    try:
        busy = _post(server, _INITIALIZE)
        assert busy.status_code == 503
    finally:
        for _ in range(MAX_CONCURRENT_REQUESTS):
            server.slots.release()
    assert _post(server, _INITIALIZE).status_code == 200


def test_idle_connections_cannot_pile_up_threads(
    listener: tuple[McpHttpServer, _FakeEngine],
) -> None:
    """The slot is taken at accept: 32 idle sockets start at most 16 threads."""
    server, _ = listener
    before = threading.active_count()
    sockets = [
        socket.create_connection(("127.0.0.1", server.server_address[1]))
        for _ in range(2 * MAX_CONCURRENT_REQUESTS)
    ]
    try:
        deadline = time.monotonic() + 5
        refused = 0
        for raw in sockets[MAX_CONCURRENT_REQUESTS:]:
            raw.settimeout(max(deadline - time.monotonic(), 0.1))
            if raw.recv(64).startswith(b"HTTP/1.1 503"):
                refused += 1
        assert refused == MAX_CONCURRENT_REQUESTS
        assert threading.active_count() - before <= MAX_CONCURRENT_REQUESTS
    finally:
        for raw in sockets:
            raw.close()


def test_http_a_stalled_body_frees_its_slot(
    listener: tuple[McpHttpServer, _FakeEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client that stops sending is cut off at the read deadline."""
    from remember import mcp_http

    monkeypatch.setattr(mcp_http._Handler, "timeout", 0.2)  # noqa: SLF001
    server, _ = listener
    with socket.create_connection(("127.0.0.1", server.server_address[1])) as raw:
        raw.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{")
        raw.settimeout(5)
        assert raw.recv(64) == b""  # closed without an answer
    assert _post(server, _INITIALIZE).status_code == 200


# ---------------------------------------------------------------------------
# Bridge mode
# ---------------------------------------------------------------------------

_REMOTE = "https://remote.test/mcp"
_KEY = "rmb_secret-key-value"


class _Remote:
    """A fake remote Streamable HTTP MCP endpoint."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.sessions: set[str] = set()
        self.tools: list[dict[str, object]] = [
            {
                "name": "facts_context",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True, "destructiveHint": False},
            },
            {
                "name": "ingest",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": False, "destructiveHint": False},
            },
            {
                "name": "account_usage",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
            },
            {"name": "query_sql", "inputSchema": {"type": "object"}},
            {
                "name": "delete_document",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
            },
        ]
        #: Scripted answers by method, consumed first.
        self.script: dict[str, list[httpx.Response]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        message = json.loads(request.content)
        method = message.get("method")
        if method in self.script and self.script[method]:
            return self.script[method].pop(0)
        if method == "initialize":
            session = f"s{len(self.sessions) + 1}"
            self.sessions.add(session)
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": session},
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
                },
            )
        if request.headers.get("Mcp-Session-Id") not in self.sessions:
            return httpx.Response(404)
        if "id" not in message:
            return httpx.Response(202)
        if method == "tools/list":
            events = (
                'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
                "data: "
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {"tools": self.tools},
                    }
                )
                + "\n\n"
            )
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=events.encode(),
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "content": [{"type": "text", "text": "ok"}],
                    "isError": False,
                },
            },
        )


def _bridge(
    remote: _Remote,
    *messages: dict[str, object],
    read_only: bool = False,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> tuple[list[dict], str]:
    output, errors = StringIO(), StringIO()
    bridge = McpBridge(
        url=_REMOTE,
        key=_KEY,
        read_only=read_only,
        http=httpx.Client(transport=httpx.MockTransport(handler or remote.handle)),
        output_stream=output,
        error_stream=errors,
    )
    lines = "\n".join(json.dumps(message) for message in messages)
    assert bridge.run(StringIO(lines)) == 0
    text = output.getvalue() + errors.getvalue()
    assert _KEY not in text
    return [
        json.loads(line) for line in output.getvalue().splitlines()
    ], errors.getvalue()


def _call(request_id: int, name: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }


_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def test_bridge_relays_json_and_event_streams_verbatim() -> None:
    remote = _Remote()
    answers, _ = _bridge(
        remote,
        _INITIALIZE,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _LIST,
        _call(3, "account_usage"),
    )
    assert [answer.get("id") for answer in answers] == [1, None, 2, 3]
    assert answers[1]["method"] == "notifications/progress"
    # Every remote tool passes, including ones this package does not know.
    assert answers[2]["result"]["tools"] == remote.tools
    posts = [request for request in remote.requests if request.method == "POST"]
    assert all(r.headers["Authorization"] == f"Bearer {_KEY}" for r in posts)
    assert "Mcp-Session-Id" not in posts[0].headers
    assert all(r.headers["Mcp-Session-Id"] == "s1" for r in posts[1:])
    assert all(r.headers["MCP-Protocol-Version"] == "2025-06-18" for r in posts[1:])
    assert remote.requests[-1].method == "DELETE"


def test_bridge_recovers_a_lost_session_once() -> None:
    remote = _Remote()
    forgotten: list[bool] = []

    def forgetful(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content or b"{}").get("id") == 3 and not forgotten:
            forgotten.append(True)
            remote.sessions.clear()
        return remote.handle(request)

    answers, _ = _bridge(
        remote, _INITIALIZE, _call(3, "facts_context"), handler=forgetful
    )
    assert [answer["id"] for answer in answers] == [1, 3]
    assert answers[1]["result"]["isError"] is False
    bodies = [json.loads(r.content) for r in remote.requests if r.method == "POST"]
    methods = [body.get("method") for body in bodies]
    assert methods == [
        "initialize",
        "tools/call",
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert bodies[2]["params"] == _INITIALIZE["params"]


def test_bridge_maps_http_failures_and_keeps_running() -> None:
    remote = _Remote()
    remote.script["tools/call"] = [
        httpx.Response(401),
        httpx.Response(403, json={"detail": "project p-x is not covered by this key"}),
        httpx.Response(429, headers={"Retry-After": "4"}),
        httpx.Response(502, text="bad gateway"),
    ]
    answers, _ = _bridge(
        remote,
        _INITIALIZE,
        _call(2, "facts_context"),
        _call(3, "facts_context"),
        _call(4, "facts_context"),
        _call(5, "facts_context"),
        _call(6, "facts_context"),
    )
    assert "remember login" in answers[1]["error"]["message"]
    assert answers[2]["error"]["message"] == "project p-x is not covered by this key"
    limited = json.loads(answers[3]["result"]["content"][0]["text"])["error"]
    assert (limited["code"], limited["retry_after"]) == ("rate_limited", 4)
    assert answers[4]["error"]["code"] == -32603
    assert answers[5]["result"]["isError"] is False


def test_bridge_network_failure_is_a_request_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    answers, _ = _bridge(_Remote(), _INITIALIZE, handler=down)
    assert answers[0]["error"]["code"] == -32603


def test_bridge_never_follows_a_cross_origin_redirect() -> None:
    remote = _Remote()
    elsewhere: list[httpx.Request] = []

    def redirecting(request: httpx.Request) -> httpx.Response:
        if request.url.host != "remote.test":
            elsewhere.append(request)
            return httpx.Response(200)
        return httpx.Response(307, headers={"Location": "https://other.test/mcp"})

    answers, _ = _bridge(remote, _INITIALIZE, handler=redirecting)
    assert "cross-origin" in answers[0]["error"]["message"]
    assert elsewhere == []


def test_bridge_follows_a_same_origin_redirect() -> None:
    remote = _Remote()

    def moved(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(308, headers={"Location": "/v2/mcp"})
        return remote.handle(request)

    answers, _ = _bridge(remote, _INITIALIZE, handler=moved)
    assert answers[0]["result"]["protocolVersion"] == "2025-06-18"


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://remote.test/mcp", False),
        ("ftp://remote.test/mcp", False),
        ("http://127.0.0.1:9000/mcp", True),
        ("https://remote.test/mcp", True),
    ],
)
def test_bridge_url_must_be_https_or_loopback_http(url: str, ok: bool) -> None:
    if ok:
        McpBridge(url=url, key=_KEY).run(StringIO(""))
    else:
        with pytest.raises(ValueError):
            McpBridge(url=url, key=_KEY)


def test_read_only_bridge_passes_only_read_catalogue_tools() -> None:
    remote = _Remote()
    answers, _ = _bridge(
        remote,
        _INITIALIZE,
        _LIST,
        _call(3, "facts_context"),
        _call(4, "ingest"),
        _call(5, "account_usage"),
        _call(6, "query_sql"),
        _call(7, "delete_document"),
        read_only=True,
    )
    listed = next(answer for answer in answers if answer.get("id") == 2)
    assert [entry["name"] for entry in listed["result"]["tools"]] == ["facts_context"]
    by_id = {answer.get("id"): answer for answer in answers}
    assert by_id[3]["result"]["isError"] is False
    for refused in (4, 5, 6, 7):
        assert _error(by_id[refused]["result"])["code"] == "read_only"
    forwarded = [
        json.loads(r.content)["params"]["name"]
        for r in remote.requests
        if r.method == "POST" and json.loads(r.content).get("method") == "tools/call"
    ]
    assert forwarded == ["facts_context"]


def test_read_only_bridge_refuses_a_call_before_any_listing() -> None:
    remote = _Remote()
    answers, _ = _bridge(remote, _INITIALIZE, _call(2, "facts_context"), read_only=True)
    assert _error(answers[1]["result"])["code"] == "read_only"


# ---------------------------------------------------------------------------
# Mode selection and the stored-key binding (CLI)
# ---------------------------------------------------------------------------


@pytest.fixture()
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Replace the three serving loops with recorders; fake the issuer metadata."""
    seen: dict[str, object] = {}

    def bridge_run(self: McpBridge, input_stream: object = None) -> int:
        seen["bridge"] = (self._url, self._key)  # noqa: SLF001
        return 0

    def engine_stdio(*, server: EngineMcpServer, **_: object) -> int:
        seen["engine"] = server
        return 0

    def http(*, bind: str, engine_url: str, read_only: bool) -> int:
        seen["http"] = (bind, engine_url, read_only)
        return 0

    def metadata(issuer: str, *, http: httpx.Client) -> IssuerMetadata:
        seen["metadata"] = issuer
        return IssuerMetadata(issuer=issuer, remember_mcp_endpoint=f"{ISSUER}/mcp")

    monkeypatch.setattr(McpBridge, "run", bridge_run)
    monkeypatch.setattr("remember.mcp_engine.serve_stdio", engine_stdio)
    monkeypatch.setattr("remember.mcp_http.serve_http", http)
    monkeypatch.setattr("remember.cli.fetch_issuer_metadata", metadata)
    return seen


def _store_key(key: str) -> None:
    from remember.credentials import config_dir

    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "credentials.json"
    path.write_text(json.dumps({"version": 2, "issuer": ISSUER, "key": key}))
    path.chmod(0o600)


def test_a_stored_signed_key_bridges_to_its_issuers_endpoint(
    recorded: dict[str, object],
) -> None:
    key = make_key()
    _store_key(key)
    assert main(["mcp"]) == 0
    assert recorded["bridge"] == (f"{ISSUER}/mcp", key)


def test_a_stored_key_is_not_sent_to_another_remote(
    recorded: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_key(make_key())
    monkeypatch.setenv("REMEMBER_MCP_URL", "https://elsewhere.test/mcp")
    assert main(["mcp"]) == 2
    assert "bridge" not in recorded


def test_an_explicit_key_goes_to_any_remote(
    recorded: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_key(make_key())
    monkeypatch.setenv("REMEMBER_API_KEY", "explicit-key")
    assert main(["mcp", "--remote-url", "https://elsewhere.test/mcp"]) == 0
    assert recorded["bridge"] == ("https://elsewhere.test/mcp", "explicit-key")


def test_an_explicit_engine_url_means_engine_mode(recorded: dict[str, object]) -> None:
    _store_key(make_key())
    assert main(["mcp", "--api-url", "https://dp-a.test"]) == 0
    assert "engine" in recorded and "bridge" not in recorded


def test_a_self_hosted_setup_is_engine_mode(
    recorded: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEMBER_API_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("REMEMBER_API_KEY", "shared-secret")
    assert main(["mcp"]) == 0
    assert "engine" in recorded and "metadata" not in recorded


@pytest.mark.parametrize(
    "argv",
    [
        ["mcp", "--api-url", "http://127.0.0.1:8000", "--remote-url", _REMOTE],
        ["mcp", "--remote-url", _REMOTE],  # no key anywhere
        ["mcp", "--remote-url", _REMOTE, "--api-key", "k", "--project", "docs"],
        ["mcp", "--transport", "http", "--remote-url", _REMOTE],
        ["mcp", "--transport", "http", "--api-key", "k"],
    ],
)
def test_contradictory_mcp_options_are_usage_errors(
    recorded: dict[str, object], argv: list[str]
) -> None:
    assert main(argv) == 2
    assert not {"bridge", "engine", "http"} & set(recorded)


def test_http_transport_uses_the_engine_url_and_no_key(
    recorded: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEMBER_API_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("REMEMBER_API_KEY", "ignored")
    assert main(["mcp", "--transport", "http", "--read-only"]) == 0
    assert recorded["http"] == ("127.0.0.1:8765", "http://127.0.0.1:8000", True)


def test_doctor_names_a_tool_served_at_another_version() -> None:
    from remember.cli import _tool_version_mismatches

    newer_engine = tool("facts_context").tool_version + 1
    lines = _tool_version_mismatches(
        _served(facts_context=newer_engine, query_sql=None)
    )
    assert len(lines) == 1
    assert "'facts_context'" in lines[0] and "upgrade remember" in lines[0]


def test_read_only_approvals_follow_the_latest_complete_listing() -> None:
    """A tool the remote stops marking read-only is refused from the next listing on."""
    remote = _Remote()

    def relabel(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if body.get("id") == 4:
            remote.tools[0] = {
                **remote.tools[0],
                "annotations": {"readOnlyHint": False},
            }
        return remote.handle(request)

    answers, _ = _bridge(
        remote,
        _INITIALIZE,
        _LIST,
        _call(3, "facts_context"),
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        _call(5, "facts_context"),
        read_only=True,
        handler=relabel,
    )
    by_id = {answer.get("id"): answer for answer in answers}
    assert by_id[3]["result"]["isError"] is False
    assert by_id[4]["result"]["tools"] == []
    assert _error(by_id[5]["result"])["code"] == "read_only"


def test_read_only_approvals_are_cleared_when_the_session_reopens() -> None:
    remote = _Remote()
    forgotten: list[bool] = []

    def forgetful(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content or b"{}").get("id") == 3 and not forgotten:
            forgotten.append(True)
            remote.sessions.clear()
        return remote.handle(request)

    answers, _ = _bridge(
        remote,
        _INITIALIZE,
        _LIST,
        _call(3, "facts_context"),
        _call(4, "facts_context"),
        read_only=True,
        handler=forgetful,
    )
    by_id = {answer.get("id"): answer for answer in answers}
    assert by_id[3]["result"]["isError"] is False  # approved before the reopen
    assert _error(by_id[4]["result"])["code"] == "read_only"  # list again first


def test_in_process_server_hides_unexpected_failures(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected exception is a logged internal_error with no internals."""
    import logging

    from rememberstack.surfaces import mcp as in_process

    monkeypatch.setattr(in_process.logger, "disabled", False)

    class _Exploding:
        deployment_id = uuid4()

        def run(self, **_: object) -> object:
            raise RuntimeError("secret connection string postgres://u:p@h")

    server = OperationMcpServer(surface=_Exploding())  # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR):
        result = server.call_tool(name="resolve_entity", arguments={"name": "A"})
    error = _error(result)
    assert error["code"] == "internal_error"
    assert "secret" not in json.dumps(error)
    assert any(record.exc_info is not None for record in caplog.records)
