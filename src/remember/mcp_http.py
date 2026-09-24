"""``remember mcp --transport http``: engine mode over Streamable HTTP (D136 §5.2.1).

One endpoint, ``/mcp``:

- ``POST`` carries one JSON-RPC message and is answered with
  ``application/json`` (``202`` with no body for a notification). The server
  never initiates messages, so ``GET`` is ``405``.
- ``initialize`` opens a session and returns its ``Mcp-Session-Id``; every
  later request must carry it. An unknown or expired session is ``404``, which
  tells the client to initialize again. ``DELETE`` ends a session. Sessions
  hold protocol state only, never memory.
- A request whose ``Origin`` is present and is not this listener's own origin
  is ``403`` — a web page cannot reach a loopback listener through a hostname
  it controls (DNS rebinding).
- **Authorisation is the engine's.** The caller's ``Authorization`` header is
  forwarded to the engine unchanged with each engine call and never stored;
  the listener holds no credential. ``tools/list`` is therefore not filtered
  by the caller's permissions, and a call the engine refuses is a tool error.

- After ``initialize`` a request may carry ``MCP-Protocol-Version``; any
  version other than the one this server negotiates is ``400``.

The listener binds to a loopback address only. To reach it from other
machines, put an authenticated reverse proxy (which also terminates TLS) in
front of it. It is bounded: at most :data:`MAX_CONCURRENT_REQUESTS` requests
at once (more get ``503``), a :data:`READ_TIMEOUT_SECONDS` deadline on every
socket read, and a :data:`MAX_BODY_BYTES` request body.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import secrets
import socket
import sys
import threading
import time
from typing import cast
from typing import Final
from urllib.parse import urlsplit

import httpx

from remember.client import MemoryClient
from remember.issuer import is_loopback
from remember.mcp_engine import dispatch
from remember.mcp_engine import EngineMcpServer
from remember.mcp_engine import MCP_PROTOCOL_VERSION
from remember.mcp_engine import rpc_error

DEFAULT_BIND: Final = "127.0.0.1:8765"
MCP_PATH: Final = "/mcp"
#: A session unused this long is forgotten (starting value).
SESSION_IDLE_SECONDS: Final = 3600.0
#: At most this many sessions are kept; the least recently used goes first.
MAX_SESSIONS: Final = 1024
#: The largest request body read. Only an ``ingest`` body sent as
#: ``content_base64`` is ever large (base64 adds a third), so this admits a
#: document of about 24 MiB; larger files belong to ``remember ingest``.
MAX_BODY_BYTES: Final = 32 * 1024 * 1024
#: Requests handled at once; more are answered ``503`` straight away.
MAX_CONCURRENT_REQUESTS: Final = 16
#: Deadline for each read from a client socket (starting value).
READ_TIMEOUT_SECONDS: Final = 30.0
_ENGINE_TIMEOUT: Final = httpx.Timeout(300.0, connect=10.0)


class HttpTransportError(ValueError):
    """The listener cannot start as configured."""


def parse_bind(value: str) -> tuple[str, int]:
    """``HOST:PORT`` (``[v6]:PORT`` for IPv6) on a loopback address."""
    host, separator, port = value.strip().rpartition(":")
    host = host.strip("[]")
    if not separator or not host or not port.isdigit() or int(port) > 65535:
        raise HttpTransportError(f"--bind must be HOST:PORT, got {value!r}")
    if not is_loopback(host):
        raise HttpTransportError(
            f"--bind {value!r} is not a loopback address: the HTTP transport"
            " listens on loopback only. To reach it from other machines, put an"
            " authenticated reverse proxy in front of it"
        )
    return host, int(port)


class _Sessions:
    """Live session ids with their last use, bounded in age and number."""

    def __init__(self) -> None:
        self._last_used: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def open(self) -> str:
        session_id = secrets.token_urlsafe(24)
        with self._lock:
            self._last_used[session_id] = time.monotonic()
            while len(self._last_used) > MAX_SESSIONS:
                self._last_used.popitem(last=False)
        return session_id

    def touch(self, session_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last_used.get(session_id)
            if last is None or now - last > SESSION_IDLE_SECONDS:
                self._last_used.pop(session_id, None)
                return False
            self._last_used[session_id] = now
            self._last_used.move_to_end(session_id)
            return True

    def close(self, session_id: str) -> bool:
        with self._lock:
            return self._last_used.pop(session_id, None) is not None


class McpHttpServer(ThreadingHTTPServer):
    """The listener: one engine URL, its sessions, and its own origins."""

    daemon_threads = True

    def __init__(
        self, *, host: str, port: int, engine_url: str, read_only: bool
    ) -> None:
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__((host, port), _Handler)
        self.engine_url = engine_url.rstrip("/")
        self.read_only = read_only
        self.sessions = _Sessions()
        self.slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        bound_port = self.server_address[1]
        hosts = {
            f"[{host}]" if ":" in host else host,
            "localhost",
            "127.0.0.1",
            "[::1]",
        }
        self.origins = frozenset(f"http://{name}:{bound_port}" for name in hosts)

    @property
    def url(self) -> str:
        """The endpoint URL this listener serves."""
        host = self.server_address[0]
        shown = f"[{host}]" if ":" in str(host) else host
        return f"http://{shown}:{self.server_address[1]}{MCP_PATH}"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    #: ``StreamRequestHandler`` applies this to the socket: every read has a deadline.
    timeout = READ_TIMEOUT_SECONDS

    @property
    def listener(self) -> McpHttpServer:
        return cast(McpHttpServer, self.server)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Request logging is off: headers carry the caller's key."""

    def do_GET(self) -> None:  # noqa: N802 — http.server naming
        self._bounded(self._get)

    def do_DELETE(self) -> None:  # noqa: N802
        self._bounded(self._delete)

    def do_POST(self) -> None:  # noqa: N802
        self._bounded(self._post)

    def _bounded(self, handle: Callable[[], None]) -> None:
        """Run one request in a free slot, or answer ``503`` at once."""
        if not self.listener.slots.acquire(blocking=False):
            self._reply(503, headers={"Retry-After": "1"}, close=True)
            return
        try:
            if not self._refused():
                handle()
        except TimeoutError:
            self.close_connection = True
        finally:
            self.listener.slots.release()

    def _get(self) -> None:
        self._reply(405, headers={"Allow": "POST, DELETE"})

    def _delete(self) -> None:
        session_id = self.headers.get("Mcp-Session-Id")
        if not session_id:
            self._reply(400, body=_error_body("Mcp-Session-Id header is required"))
            return
        self._reply(204 if self.listener.sessions.close(session_id) else 404)

    def _post(self) -> None:
        length_header = self.headers.get("Content-Length")
        if length_header is None or not length_header.isdigit():
            self._reply(411)
            return
        length = int(length_header)
        if length > MAX_BODY_BYTES:
            self._reply(413, close=True)
            return
        try:
            message = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            self._reply(
                400, body=rpc_error(request_id=None, code=-32700, message=str(error))
            )
            return
        if not isinstance(message, dict):
            self._reply(
                400,
                body=rpc_error(
                    request_id=None,
                    code=-32600,
                    message="send one JSON-RPC message per request (no batches)",
                ),
            )
            return
        initialize = message.get("method") == "initialize"
        if not initialize:
            session_id = self.headers.get("Mcp-Session-Id")
            if not session_id:
                self._reply(400, body=_error_body("Mcp-Session-Id header is required"))
                return
            version = self.headers.get("MCP-Protocol-Version")
            if version is not None and version != MCP_PROTOCOL_VERSION:
                self._reply(
                    400,
                    body=_error_body(
                        f"unsupported MCP-Protocol-Version {version!r};"
                        f" this server speaks {MCP_PROTOCOL_VERSION}"
                    ),
                )
                return
            if not self.listener.sessions.touch(session_id):
                self._reply(404, body=_error_body("unknown or expired session"))
                return
        response = self._answer(message)
        headers: dict[str, str] = {}
        if initialize and response is not None and "result" in response:
            # A session exists only once initialize has succeeded.
            headers["Mcp-Session-Id"] = self.listener.sessions.open()
        if response is None:
            self._reply(202)
        else:
            self._reply(200, body=response, headers=headers)

    def _answer(self, message: dict[str, object]) -> dict[str, object] | None:
        """Dispatch with an engine client carrying this caller's bearer."""
        authorization = self.headers.get("Authorization")
        with httpx.Client(
            base_url=self.listener.engine_url,
            headers={"Authorization": authorization} if authorization else None,
            timeout=_ENGINE_TIMEOUT,
            follow_redirects=False,
        ) as http:
            server = EngineMcpServer(
                client=MemoryClient(client=http),
                read_only=self.listener.read_only,
                path_ingest=False,
            )
            return dispatch(server=server, message=message)

    def _refused(self) -> bool:
        """Answer and return ``True`` for a wrong path or a foreign ``Origin``."""
        if urlsplit(self.path).path != MCP_PATH:
            self._reply(404)
            return True
        request_origin = self.headers.get("Origin")
        if request_origin is not None and (
            request_origin.rstrip("/").lower() not in self.listener.origins
        ):
            self._reply(403, body=_error_body("Origin not allowed"))
            return True
        return False

    def _reply(
        self,
        status: int,
        *,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        close: bool = False,
    ) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if payload:
            self.wfile.write(payload)


def _error_body(message: str) -> dict[str, object]:
    return {"error": message}


def build_server(*, bind: str, engine_url: str, read_only: bool) -> McpHttpServer:
    """Bind the listener on a loopback address (not yet serving)."""
    host, port = parse_bind(bind)
    try:
        return McpHttpServer(
            host=host, port=port, engine_url=engine_url, read_only=read_only
        )
    except OSError as error:
        raise HttpTransportError(f"cannot listen on {bind}: {error}") from error


def serve_http(*, bind: str, engine_url: str, read_only: bool) -> int:
    """Serve until interrupted."""
    server = build_server(bind=bind, engine_url=engine_url, read_only=read_only)
    print(
        f"remember mcp: serving {server.url} for the engine at {engine_url}",
        file=sys.stderr,
    )
    with server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0
