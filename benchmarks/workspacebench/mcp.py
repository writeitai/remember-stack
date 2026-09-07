"""Read-only MCP discovery by launching the current ``remember mcp`` executable."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from io import StringIO
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from benchmarks.workspacebench.env import sanitized_subprocess_env
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import sha256_bytes
from benchmarks.workspacebench.models import McpAccessBinding
from benchmarks.workspacebench.models import McpDiscovery
from benchmarks.workspacebench.models import ToolDescriptorRecord
from benchmarks.workspacebench.protocol import LOOPBACK_HOSTS
from benchmarks.workspacebench.protocol import MCP_READ_ONLY_ARGS
from benchmarks.workspacebench.protocol import REQUIRED_ASSURED_TOOLS
from benchmarks.workspacebench.supervisor import SupervisedProcessResult
from remember.mcp_memory_tools import MEMORY_WRITE_TOOL_NAMES
from remember.query_sandbox.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.remote_mcp import MCP_PROTOCOL_VERSION
from remember.remote_mcp import RemoteOperationMcpServer


class McpDiscoveryError(WorkspaceBenchError):
    """Remote MCP catalog, origin, or transport failed closed."""


StdioRunner = Callable[..., SupervisedProcessResult]


def origin_tuple(value: str) -> tuple[str, str | None, int | None, str]:
    """Return scheme, hostname, port, and path for exact origin comparison."""
    parsed = urlparse(value.rstrip("/"))
    return (
        parsed.scheme.lower(),
        None if parsed.hostname is None else parsed.hostname.lower(),
        parsed.port,
        parsed.path or "/",
    )


def origins_equal(left: str, right: str) -> bool:
    """True when two http(s) origins match scheme, host, port, and path."""
    return origin_tuple(left) == origin_tuple(right)


def is_loopback_origin(value: str) -> bool:
    """True when the origin hostname is a loopback address."""
    hostname = urlparse(value.rstrip("/")).hostname
    return hostname is not None and hostname.lower() in LOOPBACK_HOSTS


def validate_access_binding(*, access: McpAccessBinding, receipt_origin: str) -> None:
    """Refuse a binding whose declared target is not the receipt origin."""
    if not origins_equal(access.canonical_target_origin, receipt_origin):
        raise McpDiscoveryError(
            f"access binding canonical origin {access.canonical_target_origin!r} "
            f"does not match receipt origin {receipt_origin!r}"
        )
    if access.mode == "direct":
        if not origins_equal(
            access.local_access_origin, access.canonical_target_origin
        ):
            raise McpDiscoveryError(
                "direct MCP access requires local_access_origin to equal "
                "canonical_target_origin; use ssh_local_forward for a localhost tunnel"
            )
        return
    if not is_loopback_origin(access.local_access_origin):
        raise McpDiscoveryError(
            "ssh_local_forward local_access_origin must be a loopback http(s) origin"
        )
    if origins_equal(access.local_access_origin, access.canonical_target_origin):
        raise McpDiscoveryError(
            "ssh_local_forward requires local_access_origin to differ from the "
            "receipt canonical origin"
        )


def access_binding_from_args(
    *,
    api_origin: str,
    receipt_origin: str,
    access_mode: str = "direct",
    canonical_origin: str | None = None,
) -> McpAccessBinding:
    """Build a typed access binding from CLI coordinates."""
    if access_mode == "ssh_local_forward":
        target = canonical_origin or receipt_origin
        binding = McpAccessBinding(
            mode="ssh_local_forward",
            local_access_origin=api_origin.rstrip("/"),
            canonical_target_origin=target.rstrip("/"),
        )
    else:
        binding = McpAccessBinding(
            mode="direct",
            local_access_origin=api_origin.rstrip("/"),
            canonical_target_origin=(canonical_origin or receipt_origin).rstrip("/"),
        )
    validate_access_binding(access=binding, receipt_origin=receipt_origin)
    return binding


def canonical_assured_operation_names() -> tuple[str, ...]:
    """Return shipping registry names in canonical order.

    This is the live ``CANONICAL_OPERATIONS`` pin, not a Workspace-Bench copy.
    Catalog binding compares it to ``REQUIRED_ASSURED_TOOLS`` so a later
    rename fails preflight instead of leaving the harness on stale names.
    """
    from rememberstack.spine.assured_operations import CANONICAL_OPERATIONS

    return tuple(operation.name.value for operation in CANONICAL_OPERATIONS)


def reject_required_assured_tools_registry_drift() -> None:
    """Refuse catalog binding when the protocol pin no longer matches main."""
    registry = canonical_assured_operation_names()
    if registry != REQUIRED_ASSURED_TOOLS:
        raise McpDiscoveryError(
            "required assured tools drifted from the canonical registry: "
            f"pin={list(REQUIRED_ASSURED_TOOLS)} registry={list(registry)}"
        )


def catalog_sha256(records: Sequence[ToolDescriptorRecord]) -> str:
    """Fingerprint the bound tools/list descriptors including inputSchema."""
    payload = json.dumps(
        [
            {
                "name": item.name,
                "description": item.description,
                "inputSchema": item.input_schema,
            }
            for item in records
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256_bytes(payload)


def remember_launcher() -> tuple[str, ...]:
    """Return argv for the current interpreter's ``remember`` module.

    Production preflight launches this executable over stdio so ambient
    ``remember login`` credentials are resolved inside the CLI, not by the
    Workspace-Bench adapter.
    """
    return (sys.executable, "-m", "remember")


def mcp_stdio_command(
    *, remember_bin: Sequence[str] | str, api_origin: str | None
) -> tuple[str, ...]:
    """Build the memory-arm stdio command without embedding a credential."""
    prefix = (remember_bin,) if isinstance(remember_bin, str) else tuple(remember_bin)
    command = (*prefix, *MCP_READ_ONLY_ARGS)
    if api_origin:
        command = (*command, "--api-url", api_origin)
    return command


def discover_mcp(
    *,
    api_origin: str,
    receipt_origin: str,
    access: McpAccessBinding | None = None,
    remember_bin: Sequence[str] | str | None = None,
    stdio_runner: StdioRunner | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> McpDiscovery:
    """Launch ``remember mcp --read-only`` over stdio and bind tools/list.

    This is the production preflight path. It does not construct a
    ``MemoryClient`` and must not receive a token in argv, env, prompt,
    workspace, or artifacts. Ambient credentials are resolved by the launched
    ``remember`` CLI.
    """
    binding = access or access_binding_from_args(
        api_origin=api_origin, receipt_origin=receipt_origin
    )
    validate_access_binding(access=binding, receipt_origin=receipt_origin)
    argv = mcp_stdio_command(
        remember_bin=remember_bin or remember_launcher(),
        api_origin=binding.local_access_origin,
    )
    spawn_env = sanitized_subprocess_env(extra=env)
    _reject_credential_material(argv=argv, env=spawn_env)
    records, protocol_version = list_tools_over_stdio(
        argv=argv, cwd=cwd or Path.cwd(), runner=stdio_runner, env=spawn_env
    )
    return bind_listed_tools(
        records=records,
        api_origin=binding.local_access_origin,
        access=binding,
        discovered_over_stdio=True,
        negotiated_protocol_version=protocol_version,
    )


def bind_listed_tools(
    *,
    records: Sequence[ToolDescriptorRecord],
    api_origin: str,
    access: McpAccessBinding | None = None,
    discovered_over_stdio: bool,
    negotiated_protocol_version: str | None = None,
) -> McpDiscovery:
    """Validate a tools/list catalog and bind the memory-arm allowlist."""
    reject_required_assured_tools_registry_drift()
    names = tuple(record.name for record in records)
    duplicates = tuple(name for name in dict.fromkeys(names) if names.count(name) > 1)
    if duplicates:
        raise McpDiscoveryError("duplicate MCP tool names: " + ", ".join(duplicates))
    write_present = tuple(name for name in names if name in MEMORY_WRITE_TOOL_NAMES)
    if write_present:
        raise McpDiscoveryError(
            "read-only MCP advertised write tools: " + ", ".join(write_present)
        )
    missing = tuple(name for name in REQUIRED_ASSURED_TOOLS if name not in names)
    if missing:
        raise McpDiscoveryError(
            "required read tools missing from MCP catalog: " + ", ".join(missing)
        )
    open_query = tuple(name for name in names if name in OPEN_QUERY_TOOL_NAMES)
    assured = tuple(name for name in names if name not in OPEN_QUERY_TOOL_NAMES)
    bound = tuple(records)
    return McpDiscovery(
        api_origin=api_origin.rstrip("/"),
        assured_operations=assured,
        open_query_tools=open_query,
        listed_tools=bound,
        enabled_tools=names,
        write_tools_present=False,
        discovered_over_stdio=discovered_over_stdio,
        catalog_sha256=catalog_sha256(bound),
        access=access,
        negotiated_protocol_version=negotiated_protocol_version,
    )


def list_tools_over_stdio(
    *,
    argv: Sequence[str],
    cwd: Path,
    runner: StdioRunner | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[tuple[ToolDescriptorRecord, ...], str]:
    """Start ``remember mcp --read-only``, initialize, and list tools."""
    spawn_env = env if env is not None else sanitized_subprocess_env()
    _reject_credential_material(argv=argv, env=spawn_env)
    payload = (
        "\n".join(
            json.dumps(message)
            for message in (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "workspacebench-preflight",
                            "version": "1",
                        },
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
        )
        + "\n"
    )
    execute = runner or _stdio_runner
    result = execute(argv=list(argv), cwd=cwd, stdin=payload, env=spawn_env)
    if result.timed_out:
        raise McpDiscoveryError("MCP stdio tools/list timed out")
    if result.exit_code not in {0, None}:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise McpDiscoveryError(
            "remember mcp --read-only exited "
            f"{result.exit_code}: {stderr or 'no stderr'}"
        )
    by_id = _jsonrpc_responses(result.stdout)
    initialize = by_id.get(1)
    listed = by_id.get(2)
    if initialize is None:
        raise McpDiscoveryError("MCP stdio initialize returned no matching id=1")
    if listed is None:
        raise McpDiscoveryError("MCP stdio tools/list returned no matching id=2")
    _reject_rpc_error(response=initialize, label="initialize")
    _reject_rpc_error(response=listed, label="tools/list")
    initialize_result = initialize.get("result")
    protocol_version = (
        initialize_result.get("protocolVersion")
        if isinstance(initialize_result, dict)
        else None
    )
    if protocol_version != MCP_PROTOCOL_VERSION:
        raise McpDiscoveryError(
            "MCP initialize negotiated protocol "
            f"{protocol_version!r}, expected {MCP_PROTOCOL_VERSION!r}"
        )
    listed_result = listed.get("result")
    tools = listed_result.get("tools") if isinstance(listed_result, dict) else None
    if not isinstance(tools, list):
        raise McpDiscoveryError("MCP stdio tools/list did not return tools")
    records = tuple(_tool_record(item) for item in tools)
    if result.exit_code is None:
        raise McpDiscoveryError("MCP stdio process did not exit after tools/list")
    return records, str(protocol_version)


def in_process_stdio_roundtrip(
    *, server: RemoteOperationMcpServer
) -> tuple[ToolDescriptorRecord, ...]:
    """Initialize and list tools against an in-process MCP server.

    Unit-test helper only. Production preflight must launch the current
    ``remember mcp --read-only`` executable over stdio.
    """
    from remember.remote_mcp import serve_mcp_stdio

    requests = StringIO(
        "\n".join(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {
                                "name": "workspacebench-preflight",
                                "version": "1",
                            },
                        },
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            )
        )
    )
    output = StringIO()
    serve_mcp_stdio(server=server, input_stream=requests, output_stream=output)
    by_id = _jsonrpc_responses(output.getvalue().encode())
    listed = by_id.get(2)
    listed_result = None if listed is None else listed.get("result")
    initialize = by_id.get(1)
    initialize_result = None if initialize is None else initialize.get("result")
    protocol_version = (
        initialize_result.get("protocolVersion")
        if isinstance(initialize_result, dict)
        else None
    )
    if protocol_version != MCP_PROTOCOL_VERSION:
        raise McpDiscoveryError(
            "in-process MCP initialize negotiated protocol "
            f"{protocol_version!r}, expected {MCP_PROTOCOL_VERSION!r}"
        )
    if listed is None or not isinstance(listed_result, dict):
        raise McpDiscoveryError("in-process tools/list returned no tools")
    tools = listed_result.get("tools")
    if not isinstance(tools, list):
        raise McpDiscoveryError("in-process tools/list did not return tools")
    return tuple(_tool_record(item) for item in tools)


def _jsonrpc_responses(stdout: bytes) -> dict[object, dict[str, object]]:
    by_id: dict[object, dict[str, object]] = {}
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            continue
        if "id" in payload:
            by_id[payload.get("id")] = payload
    return by_id


def _reject_rpc_error(*, response: Mapping[str, object], label: str) -> None:
    error = response.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error
        raise McpDiscoveryError(f"MCP stdio {label} error: {message}")
    if "result" not in response:
        raise McpDiscoveryError(f"MCP stdio {label} response missing result")


def _reject_credential_material(
    *, argv: Sequence[str], env: Mapping[str, str] | None
) -> None:
    joined = " ".join(argv).lower()
    if "--token" in argv or "token=" in joined:
        raise McpDiscoveryError("MCP stdio argv must not contain a token")
    if env is None:
        return
    for key, value in env.items():
        lowered = key.lower()
        if "token" in lowered or "authorization" in lowered or "secret" in lowered:
            raise McpDiscoveryError(
                f"MCP stdio env must not pass credential {key}; "
                "remember mcp resolves ambient login credentials itself"
            )
        if isinstance(value, str) and value.lower().startswith("bearer "):
            raise McpDiscoveryError("MCP stdio env must not contain a bearer token")


def _tool_record(item: object) -> ToolDescriptorRecord:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise McpDiscoveryError(f"invalid MCP tool descriptor: {item!r}")
    description = item.get("description")
    schema = item.get("inputSchema")
    if schema is None:
        schema = item.get("input_schema")
    if schema is not None and not isinstance(schema, dict):
        raise McpDiscoveryError(
            f"MCP tool {item['name']!r} inputSchema must be an object"
        )
    return ToolDescriptorRecord(
        name=item["name"],
        description=description if isinstance(description, str) else "",
        input_schema=dict(schema) if isinstance(schema, dict) else {},
    )


def _stdio_runner(
    *, argv: Sequence[str], cwd: Path, stdin: str, env: Mapping[str, str] | None = None
) -> SupervisedProcessResult:
    import subprocess

    try:
        completed = subprocess.run(  # noqa: S603 -- argv is adapter-owned
            list(argv),
            cwd=str(cwd),
            input=stdin.encode(),
            capture_output=True,
            check=False,
            timeout=30,
            env=sanitized_subprocess_env() if env is None else dict(env),
        )
    except subprocess.TimeoutExpired as error:
        return SupervisedProcessResult(
            exit_code=None,
            timed_out=True,
            stdout=error.stdout or b"",
            stderr=error.stderr or b"",
        )
    return SupervisedProcessResult(
        exit_code=completed.returncode,
        timed_out=False,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def origins_match(*, receipt_origin: str, configured_origin: str) -> bool:
    """Exact origin equality for a direct HTTPS/HTTP endpoint."""
    return origins_equal(receipt_origin, configured_origin)


__all__ = (
    "McpDiscoveryError",
    "StdioRunner",
    "access_binding_from_args",
    "bind_listed_tools",
    "canonical_assured_operation_names",
    "catalog_sha256",
    "discover_mcp",
    "in_process_stdio_roundtrip",
    "is_loopback_origin",
    "list_tools_over_stdio",
    "mcp_stdio_command",
    "origins_equal",
    "origins_match",
    "reject_required_assured_tools_registry_drift",
    "remember_launcher",
    "validate_access_binding",
)
