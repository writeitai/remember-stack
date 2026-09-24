"""``remember mcp`` in bridge mode: stdio relayed to a remote MCP endpoint (D136 §5.3).

For agents that can only launch a local MCP server. Each JSON-RPC message
read from stdin is ``POST``ed to the remote Streamable HTTP endpoint with the
key as bearer; the answer — a JSON body or an event stream — is written to
stdout in order. The remote's tools, including tools this package does not
know, pass through unchanged. The bridge reads no local files: agents send
file bodies as ``text`` or ``content_base64``.

What the bridge does itself:

- **Session.** It sends the ``Mcp-Session-Id`` the remote issued. A ``404`` on
  a session re-runs ``initialize`` with the agent's original parameters once,
  then retries the message once.
- **Failures.** ``401`` → an error telling the user to run ``remember login``;
  ``403`` → the remote's message; ``429`` on a tool call → the ``rate_limited``
  tool error; network failures and ``5xx`` → a JSON-RPC error for that
  request. The bridge keeps running.
- **Safety.** The URL must be ``https`` (or ``http`` on loopback); redirects
  are followed only within the same origin, since following another would
  hand the key to another host. The key never appears in output or logs.
- **``--read-only`` fails closed.** Only catalogue tools with ``memory:read``
  permission that the remote annotates ``readOnlyHint: true`` are listed and
  forwarded; any other call is refused without being sent.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import sys
from typing import Final
from typing import TextIO

import httpx

from remember.issuer import IssuerError
from remember.issuer import require_secure_url
from remember.issuer import send_same_origin
from remember.mcp_engine import rpc_error
from remember.mcp_tools import error_result
from remember.mcp_tools import tool
from remember.mcp_tools import ToolError

_TIMEOUT: Final = httpx.Timeout(300.0, connect=10.0)
_REINIT_ID: Final = "remember-bridge-reinitialize"
_LOGIN_HINT: Final = (
    "the key was rejected (expired, revoked, or not valid for this endpoint);"
    " run `remember login`"
)


class _Failure(Exception):
    """A message could not be relayed; ``tool_error`` replaces a tool call's result."""

    def __init__(self, message: str, *, tool_error: ToolError | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.tool_error = tool_error


class McpBridge:
    """Relay one agent's stdio session to a remote MCP endpoint."""

    def __init__(
        self,
        *,
        url: str,
        key: str,
        read_only: bool = False,
        http: httpx.Client | None = None,
        output_stream: TextIO = sys.stdout,
        error_stream: TextIO = sys.stderr,
    ) -> None:
        """Bind the endpoint and key; the URL must be https or loopback http."""
        try:
            require_secure_url(url, what="the remote MCP URL")
        except IssuerError as error:
            raise ValueError(error.detail) from None
        self._url = url
        self._key = key
        self._read_only = read_only
        self._http = http or httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
        self._out = output_stream
        self._err = error_stream
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._initialize_params: object = None
        #: Tool names the remote listed as read-only that the catalogue reads.
        self._readable: set[str] = set()

    def run(self, input_stream: TextIO = sys.stdin) -> int:
        """Relay until stdin closes, then end the remote session."""
        try:
            for line in input_stream:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as error:
                    self._write(
                        rpc_error(request_id=None, code=-32700, message=str(error))
                    )
                    continue
                if not isinstance(message, dict):
                    self._write(
                        rpc_error(
                            request_id=None,
                            code=-32600,
                            message="send one JSON-RPC message per line (no batches)",
                        )
                    )
                    continue
                self.relay(message)
        finally:
            self._end_session()
            self._http.close()
        return 0

    def relay(self, message: dict[str, object]) -> None:
        """Forward one message and write whatever the remote answers."""
        method = message.get("method")
        is_request = isinstance(method, str) and "id" in message
        request_id = message.get("id")
        if method == "initialize":
            self._initialize_params = message.get("params")
        if self._read_only and method == "tools/call":
            refusal = self._read_only_refusal(message)
            if refusal is not None:
                self._write({"jsonrpc": "2.0", "id": request_id, "result": refusal})
                return
        try:
            for answer in self._exchange(message, recover=True):
                self._write(
                    self._filtered(answer, method=method, request_id=request_id)
                )
        except _Failure as failure:
            if not is_request:
                print(f"remember mcp: {failure.message}", file=self._err)
            elif failure.tool_error is not None and method == "tools/call":
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": error_result(failure.tool_error),
                    }
                )
            else:
                self._write(
                    rpc_error(
                        request_id=request_id, code=-32603, message=failure.message
                    )
                )

    # -- read-only ---------------------------------------------------------

    def _read_only_refusal(
        self, message: dict[str, object]
    ) -> dict[str, object] | None:
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        if isinstance(name, str) and name in self._readable:
            return None
        return error_result(
            ToolError(
                code="read_only",
                detail=(
                    f"{name!r} is not a listed read-only memory tool; this MCP"
                    " server is read-only."
                ),
                status_code=None,
                retryable=False,
                agent_action="Use a tool from tools/list; this server cannot change memory.",
            )
        )

    def _filtered(
        self, answer: dict[str, object], *, method: object, request_id: object
    ) -> dict[str, object]:
        """Keep only readable tools in a ``tools/list`` answer when read-only."""
        if (
            not self._read_only
            or method != "tools/list"
            or answer.get("id") != request_id
        ):
            return answer
        result = answer.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(result, dict) or not isinstance(tools, list):
            return answer
        kept = [entry for entry in tools if _readable(entry)]
        self._readable.update(str(entry["name"]) for entry in kept)
        return {**answer, "result": {**result, "tools": kept}}

    # -- HTTP --------------------------------------------------------------

    def _exchange(
        self, message: dict[str, object], *, recover: bool
    ) -> Iterator[dict[str, object]]:
        """POST one message; yield every JSON-RPC message the remote answers."""
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version is not None:
            headers["MCP-Protocol-Version"] = self._protocol_version
        request = self._http.build_request(
            "POST", self._url, content=json.dumps(message).encode(), headers=headers
        )
        try:
            response = send_same_origin(self._http, request, stream=True)
        except IssuerError as error:
            raise _Failure(f"{error.detail}; the key was not sent there") from None
        except httpx.HTTPError as error:
            raise _Failure(
                f"the remote MCP endpoint is unreachable ({type(error).__name__})"
            ) from None
        try:
            if (
                response.status_code == 404
                and recover
                and self._session_id is not None
                and message.get("method") != "initialize"
            ):
                response.close()
                self._reinitialize()
                yield from self._exchange(message, recover=False)
                return
            self._check_status(response, message=message)
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            for answer in _messages(response):
                if message.get("method") == "initialize" and answer.get(
                    "id"
                ) == message.get("id"):
                    self._remember_protocol(answer)
                yield answer
        except httpx.HTTPError as error:
            raise _Failure(
                f"the remote MCP endpoint connection failed ({type(error).__name__})"
            ) from None
        finally:
            response.close()

    def _check_status(
        self, response: httpx.Response, *, message: dict[str, object]
    ) -> None:
        status = response.status_code
        if response.is_success:
            return
        response.read()
        if status == 401:
            raise _Failure(_LOGIN_HINT)
        if status == 403:
            raise _Failure(
                _remote_message(response) or "the remote refused the request (403)"
            )
        if status == 429:
            retry_after = _retry_after(response)
            raise _Failure(
                "the remote MCP endpoint is rate limiting this key"
                + (
                    f"; retry after {retry_after:g} s"
                    if retry_after is not None
                    else ""
                ),
                tool_error=ToolError(
                    code="rate_limited",
                    detail=_remote_message(response) or "Too many requests.",
                    status_code=429,
                    retryable=True,
                    agent_action=(
                        "Wait retry_after seconds (if given), then retry; do not"
                        " retry sooner."
                    ),
                    retry_after=retry_after,
                ),
            )
        body = _json_rpc_body(response)
        if body is not None and message.get("id") is not None:
            # A JSON-RPC error the remote sent with an HTTP error status.
            raise _Failure(_rpc_message(body) or f"the remote answered HTTP {status}")
        raise _Failure(f"the remote MCP endpoint answered HTTP {status}")

    def _reinitialize(self) -> None:
        """Open a new remote session with the agent's original parameters."""
        self._session_id = None
        request = {
            "jsonrpc": "2.0",
            "id": _REINIT_ID,
            "method": "initialize",
            "params": self._initialize_params,
        }
        for answer in self._exchange(request, recover=False):
            if answer.get("id") == _REINIT_ID and "error" in answer:
                raise _Failure("the remote refused to open a new session")
        for _ in self._exchange(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, recover=False
        ):
            pass

    def _remember_protocol(self, answer: dict[str, object]) -> None:
        result = answer.get("result")
        version = result.get("protocolVersion") if isinstance(result, dict) else None
        if isinstance(version, str):
            self._protocol_version = version

    def _end_session(self) -> None:
        """Best effort: tell the remote the session is over."""
        if self._session_id is None:
            return
        try:
            request = self._http.build_request(
                "DELETE",
                self._url,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Mcp-Session-Id": self._session_id,
                },
            )
            send_same_origin(self._http, request).close()
        except (httpx.HTTPError, IssuerError):
            pass

    def _write(self, message: dict[str, object]) -> None:
        self._out.write(json.dumps(message) + "\n")
        self._out.flush()


def _readable(entry: object) -> bool:
    """A remote tool the read-only bridge may pass: a catalogue read tool the remote marks read-only."""
    if not isinstance(entry, dict):
        return False
    name = entry.get("name")
    annotations = entry.get("annotations")
    if not isinstance(name, str) or not isinstance(annotations, dict):
        return False
    if annotations.get("readOnlyHint") is not True:
        return False
    try:
        return tool(name).permission == "memory:read"
    except KeyError:
        return False


def _messages(response: httpx.Response) -> Iterator[dict[str, object]]:
    """The JSON-RPC messages of a ``2xx`` answer: a JSON body or an event stream."""
    if response.status_code == 202:
        return
    content_type = (
        response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    )
    if content_type == "text/event-stream":
        data: list[str] = []
        for line in response.iter_lines():
            if line == "":
                if data:
                    yield from _decoded("\n".join(data))
                data = []
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
        if data:
            yield from _decoded("\n".join(data))
        return
    body = response.read()
    if body.strip():
        yield from _decoded(body.decode())


def _decoded(text: str) -> Iterator[dict[str, object]]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise _Failure(
            "the remote MCP endpoint sent a message that is not JSON"
        ) from None
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            yield item


def _json_rpc_body(response: httpx.Response) -> dict[str, object] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) and body.get("jsonrpc") == "2.0" else None


def _rpc_message(body: dict[str, object]) -> str | None:
    error = body.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return message if isinstance(message, str) else None


def _remote_message(response: httpx.Response) -> str | None:
    """A short human message from an error body, whatever its shape."""
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:500] or None
    if not isinstance(body, dict):
        return None
    rpc = _rpc_message(body)
    if rpc:
        return rpc
    for field in ("error_description", "detail", "message", "error"):
        value = body.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After", "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None
