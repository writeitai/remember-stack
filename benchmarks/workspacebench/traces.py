"""Runtime event classification, secret redaction, and retrieval diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any

from benchmarks.workspacebench.hashing import atomic_write_bytes
from benchmarks.workspacebench.models import McpCallRecord
from benchmarks.workspacebench.models import MemoryDiagnostics
from benchmarks.workspacebench.protocol import ALLOWED_RUNTIME_ITEM_TYPES
from benchmarks.workspacebench.protocol import AUTH_CACHE_BASENAME
from benchmarks.workspacebench.protocol import CANARY_SECRET_FILENAME
from benchmarks.workspacebench.protocol import CREDENTIAL_REDACT_KEYS
from benchmarks.workspacebench.protocol import DISALLOWED_RUNTIME_ITEM_TYPES
from benchmarks.workspacebench.protocol import FAKE_CANARY_SECRET
from benchmarks.workspacebench.protocol import MCP_SERVER_NAME
from benchmarks.workspacebench.protocol import OWNER_ONLY_FILE_MODE
from benchmarks.workspacebench.protocol import SECRET_REDACT_KEYS


def redact_credentials(value: object, *, secrets: Sequence[str] = ()) -> object:
    """Remove credential-shaped keys and known secret values. Keep result bodies."""
    return _redact(value, drop_keys=CREDENTIAL_REDACT_KEYS, secrets=secrets)


def redact_bodies(value: object, *, secrets: Sequence[str] = ()) -> object:
    """Aggressively drop result/output bodies and credential-shaped keys."""
    return _redact(value, drop_keys=SECRET_REDACT_KEYS, secrets=secrets)


def redact_value(value: object) -> object:
    """Body-redacted sanitizer used by sanitized traces."""
    return redact_bodies(value, secrets=(FAKE_CANARY_SECRET,))


def write_jsonl(
    *,
    path: Path,
    rows: Sequence[Mapping[str, object]],
    secrets: Sequence[str] = (FAKE_CANARY_SECRET,),
    mode: int | None = OWNER_ONLY_FILE_MODE,
) -> None:
    """Atomically write a JSONL trace. Fake secrets are never persisted."""
    encoded = "".join(
        json.dumps(
            _strip_secrets(dict(row), secrets=secrets),
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
        for row in rows
    ).encode()
    atomic_write_bytes(path=path, content=encoded, mode=mode)


def classify_runtime_events(
    *,
    events: Sequence[Mapping[str, Any]],
    arm: str,
    enabled_tools: Sequence[str],
    workspace: Path,
    fake_secret: str | None = None,
) -> tuple[tuple[str, ...], tuple[McpCallRecord, ...], MemoryDiagnostics | None]:
    """Return protocol-violation reasons, MCP records, and memory diagnostics.

    Classification inspects unredacted in-memory payloads so returned context
    bytes, zero-result status, latency, and attributable paths are real.
    """
    violations: list[str] = []
    mcp_calls: list[McpCallRecord] = []
    for event in events:
        item_type = str(event.get("item_type") or event.get("type") or "")
        payload = event.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}
        if item_type not in ALLOWED_RUNTIME_ITEM_TYPES:
            violations.append(f"unknown runtime item type: {item_type}")
        if item_type in DISALLOWED_RUNTIME_ITEM_TYPES:
            violations.append(f"disallowed runtime item: {item_type}")
        if item_type == "WebSearchThreadItem":
            violations.append("web search is a protocol violation")
        if item_type == "SubAgentActivityThreadItem":
            violations.append("subagent is a protocol violation")
        if item_type == "McpToolCallThreadItem":
            server = str(payload_dict.get("server") or "")
            tool = str(payload_dict.get("tool") or "")
            if arm == "native":
                violations.append(f"native arm used MCP {server}:{tool}")
            elif server != MCP_SERVER_NAME:
                violations.append(f"unknown MCP server: {server}")
            elif tool not in enabled_tools:
                violations.append(f"MCP tool not on allowlist: {tool}")
            mcp_calls.append(
                _mcp_record(payload=payload_dict, server=server, tool=tool)
            )
        if item_type == "FileChangeThreadItem":
            for change in payload_dict.get("changes") or ():
                if not isinstance(change, dict):
                    continue
                path = change.get("path")
                if isinstance(path, str) and _escapes_workspace(
                    workspace=workspace, path=path
                ):
                    violations.append(f"write outside task workspace: {path}")
        if item_type == "CommandExecutionThreadItem":
            command = str(payload_dict.get("command") or "")
            if AUTH_CACHE_BASENAME in command or (
                fake_secret is not None and fake_secret in command
            ):
                violations.append("command attempted to read a credential location")
            cwd = payload_dict.get("cwd")
            if isinstance(cwd, str) and _escapes_workspace(
                workspace=workspace, path=cwd
            ):
                violations.append(f"command cwd outside task workspace: {cwd}")
        if payload_dict.get("approvalRequested") or payload_dict.get(
            "approval_requested"
        ):
            violations.append("approval request is a protocol violation")
        if (
            payload_dict.get("networkAccess") is True
            or payload_dict.get("network_access") is True
        ):
            violations.append("network escalation is a protocol violation")
        serialized = json.dumps(payload_dict, default=str)
        if fake_secret is not None and fake_secret in serialized:
            violations.append("fake credential entered the Codex trace")
    diagnostics = None
    if arm == "memory":
        diagnostics = memory_diagnostics(calls=mcp_calls)
    return tuple(dict.fromkeys(violations)), tuple(mcp_calls), diagnostics


def memory_diagnostics(*, calls: Sequence[McpCallRecord]) -> MemoryDiagnostics:
    """Aggregate query count, latency, empty results, and attributed paths."""
    latencies = tuple(
        call.duration_ms for call in calls if call.duration_ms is not None
    )
    paths: list[str] = []
    for call in calls:
        paths.extend(call.attributable_paths)
    return MemoryDiagnostics(
        query_count=len(calls),
        zero_result_count=sum(1 for call in calls if call.zero_result),
        latencies_ms=latencies,
        returned_context_bytes=sum(call.returned_context_bytes for call in calls),
        attributable_paths=tuple(dict.fromkeys(paths)),
    )


def scan_for_secret(
    *, root: Path, secret: str, ignore: tuple[Path, ...] = ()
) -> tuple[str, ...]:
    """Return relative artifact paths that contain ``secret``."""
    ignored = {path.resolve() for path in ignore}
    hits: list[str] = []
    if not root.exists():
        return ()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.resolve() in ignored or path.name == CANARY_SECRET_FILENAME:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if secret.encode() in data:
                hits.append(path.relative_to(root).as_posix())
            continue
        if secret in text:
            hits.append(path.relative_to(root).as_posix())
    return tuple(hits)


def _mcp_record(*, payload: Mapping[str, Any], server: str, tool: str) -> McpCallRecord:
    arguments = redact_credentials(
        payload.get("arguments"), secrets=(FAKE_CANARY_SECRET,)
    )
    result = payload.get("result")
    if result is None:
        result = payload.get("output")
    result_text = json.dumps(result, default=str) if result is not None else ""
    if FAKE_CANARY_SECRET in result_text:
        result_text = ""
        result = None
    paths = _paths_from_payload(payload if result is None else {"result": result})
    zero = _is_zero_result(result)
    duration = payload.get("duration_ms")
    if duration is None:
        duration = payload.get("durationMs")
    return McpCallRecord(
        server=server or MCP_SERVER_NAME,
        tool=tool or "unknown",
        arguments=arguments,
        duration_ms=duration if isinstance(duration, int) and duration >= 0 else None,
        is_error=bool(payload.get("error"))
        or str(payload.get("status") or "") == "failed",
        returned_context_bytes=len(result_text.encode()),
        attributable_paths=paths,
        zero_result=zero,
    )


def _paths_from_payload(payload: Mapping[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    blob = json.dumps(payload, default=str)
    for token in _extract_quoted_paths(blob):
        found.append(token)
    return tuple(dict.fromkeys(found))


def _extract_quoted_paths(blob: str) -> tuple[str, ...]:
    import re

    matches = re.findall(r"(?:[\w.-]+/)+[\w.-]+", blob)
    return tuple(matches[:32])


def _is_zero_result(result: object) -> bool:
    if result is None:
        return True
    if result == [] or result == {}:
        return True
    if isinstance(result, dict):
        items = result.get("items") or result.get("results") or result.get("rows")
        if items == []:
            return True
        if result.get("unknown") is True:
            return True
        content = result.get("content")
        if isinstance(content, list) and not content:
            return True
    return False


def _escapes_workspace(*, workspace: Path, path: str) -> bool:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        candidate.resolve().relative_to(workspace.resolve())
    except ValueError:
        return True
    return False


def _redact(
    value: object, *, drop_keys: frozenset[str], secrets: Sequence[str]
) -> object:
    if isinstance(value, dict):
        return {
            key: _redact(nested, drop_keys=drop_keys, secrets=secrets)
            for key, nested in value.items()
            if isinstance(key, str) and key not in drop_keys
        }
    if isinstance(value, list):
        return [_redact(item, drop_keys=drop_keys, secrets=secrets) for item in value]
    if isinstance(value, str):
        if any(secret and secret in value for secret in secrets):
            return "[redacted]"
        if _looks_like_secret(value):
            return "[redacted]"
    return value


def _strip_secrets(value: object, *, secrets: Sequence[str]) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_secrets(nested, secrets=secrets)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_strip_secrets(item, secrets=secrets) for item in value]
    if isinstance(value, str) and any(secret and secret in value for secret in secrets):
        return "[redacted-canary]"
    return value


def judge_execution_trace(
    events: Sequence[Mapping[str, Any]], *, secrets: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Map SDK thread items to the pinned judge ``type=tool|text`` snapshot."""
    out: list[dict[str, Any]] = []
    for event in events:
        item_type = str(event.get("item_type") or event.get("type") or "")
        payload = event.get("payload")
        payload_dict = payload if isinstance(payload, dict) else dict(event)
        timestamp = payload_dict.get("timestamp") or payload_dict.get("createdAt")
        if item_type == "CommandExecutionThreadItem":
            out.append(
                {
                    "type": "tool",
                    "tool": "command",
                    "input": {"command": payload_dict.get("command")},
                    "output": {
                        "exit_code": payload_dict.get("exit_code")
                        if payload_dict.get("exit_code") is not None
                        else payload_dict.get("exitCode"),
                        "aggregated_output": payload_dict.get("aggregated_output")
                        or payload_dict.get("aggregatedOutput")
                        or payload_dict.get("output"),
                    },
                    "timestamp": timestamp,
                }
            )
        elif item_type == "McpToolCallThreadItem":
            arguments = payload_dict.get("arguments")
            result = payload_dict.get("result")
            if result is None:
                result = payload_dict.get("output")
            out.append(
                {
                    "type": "tool",
                    "tool": payload_dict.get("tool"),
                    "input": arguments if isinstance(arguments, dict) else {},
                    "output": result if isinstance(result, dict) else {},
                    "timestamp": timestamp,
                }
            )
        elif item_type == "FileChangeThreadItem":
            out.append(
                {
                    "type": "tool",
                    "tool": "file_change",
                    "input": {"changes": payload_dict.get("changes") or []},
                    "output": {},
                    "timestamp": timestamp,
                }
            )
        elif item_type in {
            "AgentMessageThreadItem",
            "UserMessageThreadItem",
            "ReasoningThreadItem",
        }:
            content = payload_dict.get("text")
            if content is None:
                content = payload_dict.get("content")
            if isinstance(content, list):
                content = json.dumps(content, default=str)
            if isinstance(content, str) and content:
                out.append(
                    {
                        "type": "text",
                        "role": (
                            "user"
                            if item_type == "UserMessageThreadItem"
                            else "assistant"
                        ),
                        "content": content,
                        "timestamp": timestamp,
                    }
                )
    redacted: list[dict[str, Any]] = []
    for item in out:
        cleaned = redact_credentials(item, secrets=secrets)
        if isinstance(cleaned, dict):
            redacted.append(cleaned)
    return redacted


def _looks_like_secret(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.startswith("bearer ")
        or lowered.startswith("sk-")
        or "auth.json" in lowered
        or "-----begin" in lowered
    )
