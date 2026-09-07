"""Synthetic runner, preflight, timeout, and protocol-violation proofs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from datetime import timezone
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from benchmarks.workspacebench.canary import evaluate_canary
from benchmarks.workspacebench.cli import main as workspacebench_main
from benchmarks.workspacebench.codex import attest_chatgpt_account
from benchmarks.workspacebench.codex import CodexAccountError
from benchmarks.workspacebench.codex import CodexRuntimeEvent
from benchmarks.workspacebench.codex import CodexTaskRequest
from benchmarks.workspacebench.codex import CodexTaskTurn
from benchmarks.workspacebench.codex import native_arm_configuration
from benchmarks.workspacebench.errors import LiveGateError
from benchmarks.workspacebench.hashing import atomic_write_json
from benchmarks.workspacebench.hashing import sha256_bytes
from benchmarks.workspacebench.hashing import sha256_file
from benchmarks.workspacebench.hashing import tree_digest
from benchmarks.workspacebench.hashing import WorkspacePathError
from benchmarks.workspacebench.mcp import discover_mcp
from benchmarks.workspacebench.mcp import mcp_stdio_command
from benchmarks.workspacebench.mcp import remember_launcher
from benchmarks.workspacebench.models import CloudDeploymentReceipt
from benchmarks.workspacebench.models import ReadinessPin
from benchmarks.workspacebench.models import UpstreamInspection
from benchmarks.workspacebench.pair import run_pair
from benchmarks.workspacebench.preflight import run_preflight
from benchmarks.workspacebench.protocol import FAKE_CANARY_SECRET
from benchmarks.workspacebench.protocol import REQUIRED_ASSURED_TOOLS
from benchmarks.workspacebench.protocol import TARGET_OUTPUT_DIR
from benchmarks.workspacebench.protocol import UPSTREAM_COMMIT
from benchmarks.workspacebench.runner import run_agent
from benchmarks.workspacebench.supervisor import run_process_group
from benchmarks.workspacebench.supervisor import SupervisedProcessResult
from benchmarks.workspacebench.task_contract import local_task_contract
import pytest

from remember.mcp_memory_tools import MEMORY_WRITE_TOOL_NAMES
from remember.query_sandbox.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.remote_mcp import MCP_PROTOCOL_VERSION

HIDDEN_SENTINEL = "HIDDEN_RUBRIC_SENTINEL"


def _role_workspace(root: Path) -> Path:
    workspace = root / "role-workspace"
    workspace.mkdir()
    (workspace / "notes-root.txt").write_text("role", encoding="utf-8")
    (workspace / "slides.ppt").write_bytes(b"PPT")
    (workspace / "legacy.xls").write_bytes(b"XLS")
    (workspace / "modern.xlsx").write_bytes(b"XLSX")
    return workspace


def _task_dir(root: Path) -> Path:
    task = root / "task-300"
    (task / "data").mkdir(parents=True)
    (task / "data" / "notes.txt").write_text("staged-notes", encoding="utf-8")
    metadata = {
        "id": "300",
        "task": "Write report.md, table.csv, and result.json.",
        "output_files": ["report.md", "table.csv", "result.json"],
        "data_manifest": [
            {
                "stored_relpath": "data/notes.txt",
                "target_path": "inputs/notes.txt",
                "filename": "notes.txt",
            }
        ],
        "rubrics": [HIDDEN_SENTINEL],
        "rubric_types": ["gold"],
        "file_dep_graph": {"edges": []},
        "reference_output": "gold-output",
    }
    (task / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return task


def _workspace_with_metadata(root: Path) -> Path:
    """Legacy helper for run-agent tests that already have a prepared workspace."""
    workspace = root / "workspace"
    (workspace / TARGET_OUTPUT_DIR).mkdir(parents=True)
    (workspace / "notes.txt").write_text("n", encoding="utf-8")
    metadata = {
        "id": "300",
        "task": "Write report.md, table.csv, and result.json.",
        "output_files": ["report.md", "table.csv", "result.json"],
        "data_manifest": [],
    }
    (workspace / "agent-prompt.txt").write_text(metadata["task"], encoding="utf-8")
    return workspace


def _receipt(
    *, digest: str, origin: str = "http://127.0.0.1:18000"
) -> CloudDeploymentReceipt:
    return CloudDeploymentReceipt(
        workspace_digest=digest,
        rememberstack_revision="8fad369d341950b869dd2f3f8acbce4693b63cea",
        converter_router_configuration="pinned-router",
        component_generations={"e0": "1"},
        version_ids=("11111111-1111-1111-1111-111111111111",),
        readiness_requirements=ReadinessPin(
            pipeline=True, p1=True, live_graph=True, p3=False
        ),
        api_origin=origin,
        deployment_id="dep-1",
        sealed=True,
        attested_by="operator",
        attested_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )


def _tool_names() -> list[str]:
    return [*REQUIRED_ASSURED_TOOLS, *OPEN_QUERY_TOOL_NAMES]


def _stdio_mcp_runner(
    *, argv: Sequence[str], cwd: Path, stdin: str, env=None
) -> SupervisedProcessResult:
    del cwd
    assert "--token" not in argv
    assert "token=" not in " ".join(argv).lower()
    if env:
        for key in env:
            assert "token" not in key.lower()
            assert "authorization" not in key.lower()
    tools = [
        {
            "name": name,
            "description": name,
            "inputSchema": {"type": "object", "properties": {}},
        }
        for name in _tool_names()
    ]
    stdout = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "rememberstack", "version": "test"},
                },
            }
        )
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}})
        + "\n"
    )
    return SupervisedProcessResult(
        exit_code=0, timed_out=False, stdout=stdout.encode(), stderr=b""
    )


def _successful_turn(
    *, workspace: Path, extra_events: tuple[CodexRuntimeEvent, ...] = ()
) -> CodexTaskTurn:
    out = workspace / TARGET_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text("# report", encoding="utf-8")
    (out / "table.csv").write_text("a,b\n", encoding="utf-8")
    (out / "result.json").write_text("{}\n", encoding="utf-8")
    events = (
        CodexRuntimeEvent(
            item_type="CommandExecutionThreadItem",
            payload={"command": "ls", "cwd": str(workspace)},
        ),
        CodexRuntimeEvent(
            item_type="FileChangeThreadItem",
            payload={"changes": [{"path": str(out / "report.md")}]},
        ),
        *extra_events,
    )
    return CodexTaskTurn(
        status="completed",
        error_message=None,
        final_response=(
            f"['{TARGET_OUTPUT_DIR}/report.md','{TARGET_OUTPUT_DIR}/table.csv',"
            f"'{TARGET_OUTPUT_DIR}/result.json']"
        ),
        tokens_in=10,
        tokens_out=4,
        item_types=tuple(event.item_type for event in events),
        events=events,
    )


def _passing_canary(*, request: CodexTaskRequest) -> CodexTaskTurn:
    from benchmarks.workspacebench.protocol import CANARY_SECRET_FILENAME

    path = ""
    for line in request.prompt.splitlines():
        if CANARY_SECRET_FILENAME in line:
            path = line.strip()
            break
    return CodexTaskTurn(
        status="completed",
        error_message=None,
        final_response="cannot read the file: permission denied",
        tokens_in=3,
        tokens_out=2,
        item_types=("CommandExecutionThreadItem",),
        events=(
            CodexRuntimeEvent(
                item_type="CommandExecutionThreadItem",
                payload={
                    "command": f"cat {path}",
                    "exit_code": 1,
                    "aggregated_output": "Permission denied",
                },
            ),
        ),
    )


def _plant_office(upstream: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("docx", "pdf"):
        skill = upstream / "evaluation/skills/office" / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    monkeypatch.setattr(
        "benchmarks.workspacebench.office.required_office_executables",
        lambda: ("soffice", "pdftoppm"),
    )


def _pin_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    upstream = tmp_path / "upstream"
    hashes: dict[str, str] = {}
    from benchmarks.workspacebench import protocol

    for relative, _expected in protocol.EXPECTED_UPSTREAM_FILE_SHA256.items():
        path = upstream / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pin")
        hashes[relative] = __import__("hashlib").sha256(b"pin").hexdigest()
    monkeypatch.setattr(
        "benchmarks.workspacebench.upstream.EXPECTED_UPSTREAM_FILE_SHA256", hashes
    )
    monkeypatch.setattr(
        "benchmarks.workspacebench.protocol.EXPECTED_UPSTREAM_FILE_SHA256", hashes
    )
    return upstream


def test_account_attestation_rejects_api_key_without_secrets() -> None:
    with pytest.raises(CodexAccountError, match="ChatGPT"):
        attest_chatgpt_account(reader=lambda: "api_key")
    attestation = attest_chatgpt_account(
        reader=lambda: "chatgpt", now=lambda: datetime(2026, 9, 7, tzinfo=timezone.utc)
    )
    dumped = attestation.model_dump_json()
    assert "chatgpt" in dumped
    assert "auth.json" not in dumped
    assert "token" not in dumped


def test_preflight_rejects_wrong_and_dirty_upstream(tmp_path: Path) -> None:
    workspace = _role_workspace(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    for relative in (
        "evaluation/scripts/run_isolated_benchmark.py",
        "evaluation/src/task_container_entry.py",
        "evaluation/src/agent_as_a_judge.py",
        "evaluation/src/agent_runner.py",
        "evaluation/src/agents/codex.py",
        "evaluation/src/filesys_utils.py",
        "evaluation/src/task_patches.py",
        "LICENSE",
    ):
        path = upstream / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-the-pinned-bytes", encoding="utf-8")

    def git(args: Sequence[str], cwd: Path) -> str:
        del cwd
        if args[0] == "rev-parse":
            return "deadbeef\n"
        return " M evaluation/src/agent_runner.py\n"

    report = run_preflight(
        upstream=upstream,
        workspace=workspace,
        output=tmp_path / "out",
        git=git,
        skip_account=True,
        skip_mcp=True,
        skip_office=True,
        arm="native",
    )
    assert report.ok is False
    assert any("commit" in item or "dirty" in item for item in report.failures)
    assert report.canary is not None
    assert report.canary.live_turn is False
    assert "structural" in report.canary.detail


def test_preflight_accepts_pinned_checkout_when_hashes_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    upstream = _pin_upstream(tmp_path, monkeypatch)

    def git(args: Sequence[str], cwd: Path) -> str:
        del cwd
        if args[0] == "rev-parse":
            return UPSTREAM_COMMIT + "\n"
        return ""

    report = run_preflight(
        upstream=upstream,
        workspace=workspace,
        output=tmp_path / "out",
        git=git,
        skip_account=True,
        skip_mcp=True,
        skip_office=True,
        arm="native",
    )
    assert report.ok is True
    assert report.upstream.commit == UPSTREAM_COMMIT
    assert report.upstream.dirty is False


def test_mcp_discovery_launches_stdio_and_binds_allowlist(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def runner(
        *, argv: Sequence[str], cwd: Path, stdin: str, env=None
    ) -> SupervisedProcessResult:
        seen["argv"] = list(argv)
        seen["stdin"] = stdin
        seen["env"] = env
        return _stdio_mcp_runner(argv=argv, cwd=cwd, stdin=stdin, env=env)

    discovery = discover_mcp(
        api_origin="http://127.0.0.1:18000",
        receipt_origin="http://127.0.0.1:18000",
        stdio_runner=runner,
        cwd=tmp_path,
    )
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert "mcp" in argv
    assert "--read-only" in argv
    assert "--token" not in argv
    assert "initialize" in str(seen["stdin"])
    assert "tools/list" in str(seen["stdin"])
    names = {item.name for item in discovery.listed_tools}
    assert names.isdisjoint(MEMORY_WRITE_TOOL_NAMES)
    assert set(REQUIRED_ASSURED_TOOLS) <= names
    assert set(OPEN_QUERY_TOOL_NAMES) <= names
    assert discovery.write_tools_present is False
    assert discovery.discovered_over_stdio is True
    assert discovery.catalog_sha256
    command = mcp_stdio_command(
        remember_bin=remember_launcher(), api_origin="http://127.0.0.1:18000"
    )
    assert "--token" not in command


def test_composed_run_pair_hides_evaluator_metadata_and_stages_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    task_dir = _task_dir(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _plant_office(upstream, monkeypatch)
    digest = tree_digest(workspace)
    receipt_path = tmp_path / "receipt.json"
    atomic_write_json(path=receipt_path, value=_receipt(digest=digest))
    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )
    scans: list[dict[str, object]] = []

    def _scan(root: Path) -> tuple[bool, bool]:
        hidden = False
        staged = False
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if HIDDEN_SENTINEL in text:
                hidden = True
            if path.name == "notes.txt" and "staged-notes" in text:
                staged = True
        return hidden, staged

    def native_runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        assert "augmentation" not in request.prompt
        assert request.arm.mcp_enabled is False
        assert TARGET_OUTPUT_DIR in request.prompt
        hidden, staged = _scan(request.workspace)
        parent_hidden, _parent_staged = _scan(request.workspace.parent)
        scans.append(
            {"arm": "native", "hidden": hidden or parent_hidden, "staged": staged}
        )
        assert hidden is False
        assert parent_hidden is False
        assert staged is True
        assert (request.workspace / "inputs" / "notes.txt").is_file()
        assert not (request.workspace / "metadata.json").exists()
        return _successful_turn(workspace=request.workspace)

    def memory_runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        assert request.prompt.endswith(
            "Write report.md, table.csv, and result.json."
        ) or ("Write report.md, table.csv, and result.json." in request.prompt)
        assert "augmentation" in request.prompt
        assert "facts_context" in request.arm.enabled_tools
        hidden, staged = _scan(request.workspace)
        parent_hidden, _ = _scan(request.workspace.parent)
        scans.append(
            {"arm": "memory", "hidden": hidden or parent_hidden, "staged": staged}
        )
        assert hidden is False
        assert parent_hidden is False
        assert staged is True
        return _successful_turn(
            workspace=request.workspace,
            extra_events=(
                CodexRuntimeEvent(
                    item_type="McpToolCallThreadItem",
                    payload={
                        "server": "remember",
                        "tool": "facts_context",
                        "arguments": {"query": "inventory"},
                        "duration_ms": 12,
                        "result": {
                            "items": [{"path": "inputs/notes.txt", "text": "x" * 20}]
                        },
                    },
                ),
            ),
        )

    preflight, pair, native, memory = run_pair(
        upstream=upstream,
        workspace=workspace,
        output=tmp_path / "pair",
        task_dir=task_dir,
        task_prompt=None,
        execute=True,
        receipt_path=receipt_path,
        api_origin="http://127.0.0.1:18000",
        account_reader=lambda: "chatgpt",
        stdio_runner=_stdio_mcp_runner,
        live_canary_runner=_passing_canary,
        native_turn_runner=native_runner,
        memory_turn_runner=memory_runner,
        task_contract=local_task_contract(),
    )
    assert preflight.mcp is not None
    assert preflight.mcp.discovered_over_stdio is True
    assert pair.native_workspace_digest == pair.memory_workspace_digest
    assert native.workspace_digest == memory.workspace_digest
    assert native.failure_class == "none"
    assert memory.failure_class == "none"
    assert native.account is not None
    assert native.account.account_type == "chatgpt"
    assert native.arm_configuration.mcp_enabled is False
    assert memory.arm_configuration.mcp_enabled is True
    assert memory.memory is not None
    assert memory.memory.query_count == 1
    assert memory.memory.returned_context_bytes > 0
    assert FAKE_CANARY_SECRET not in native.model_dump_json()
    assert FAKE_CANARY_SECRET not in memory.model_dump_json()
    assert scans and all(item["hidden"] is False for item in scans)
    native_meta = (tmp_path / "pair" / "native" / "metadata.json").read_text(
        encoding="utf-8"
    )
    assert HIDDEN_SENTINEL in native_meta
    assert (tmp_path / "pair" / "native" / "agent.json").is_file()
    assert (tmp_path / "pair" / "native" / "output" / "report.md").is_file()
    native_meta_obj = json.loads(native_meta)
    assert native_meta_obj["__metadata_path"] == str(task_dir / "metadata.json")
    agent = json.loads((tmp_path / "pair" / "native" / "agent.json").read_text())
    trace_types = {item.get("type") for item in agent["trace"]["executionTrace"]}
    assert "tool" in trace_types
    from benchmarks.workspacebench.report import reconstruct_from_output_dir

    reconstructed = reconstruct_from_output_dir(tmp_path / "pair")
    assert reconstructed.native.arm_execution_fingerprint == pair.native_arm_fingerprint
    assert reconstructed.memory.arm_execution_fingerprint == pair.memory_arm_fingerprint
    assert preflight.codex_runtime is not None
    assert preflight.protocol.codex_cli_sha256 == preflight.codex_runtime.cli_sha256
    assert native.protocol.codex_cli_sha256 == preflight.codex_runtime.cli_sha256
    assert memory.protocol.codex_cli_sha256 == preflight.codex_runtime.cli_sha256
    assert native.protocol.protocol_fingerprint == pair.protocol_fingerprint
    assert memory.protocol.protocol_fingerprint == pair.protocol_fingerprint
    assert reconstructed.native.protocol.codex_cli_sha256 == (
        preflight.codex_runtime.cli_sha256
    )
    assert pair.live_canary is not None
    assert pair.live_canary.passed is True
    assert FAKE_CANARY_SECRET not in pair.model_dump_json()
    assert (
        tmp_path / "pair" / "native" / "workspace" / ".agents/skills/docx" / "SKILL.md"
    ).is_file()


def test_execute_fails_closed_when_skip_flags_or_canary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    task_dir = _task_dir(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _plant_office(upstream, monkeypatch)
    digest = tree_digest(workspace)
    receipt_path = tmp_path / "receipt.json"
    atomic_write_json(path=receipt_path, value=_receipt(digest=digest))
    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )
    called = {"native": 0, "memory": 0, "canary": 0}

    def native_runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        called["native"] += 1
        return _successful_turn(workspace=request.workspace)

    def memory_runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        called["memory"] += 1
        return _successful_turn(workspace=request.workspace)

    with pytest.raises(LiveGateError, match="cannot skip"):
        run_pair(
            upstream=upstream,
            workspace=workspace,
            output=tmp_path / "skip-mcp",
            task_dir=task_dir,
            execute=True,
            receipt_path=receipt_path,
            api_origin="http://127.0.0.1:18000",
            skip_mcp=True,
            skip_account=True,
            skip_office=True,
            native_turn_runner=native_runner,
            memory_turn_runner=memory_runner,
            task_contract=local_task_contract(),
        )
    assert called == {"native": 0, "memory": 0, "canary": 0}

    def failing_canary(*, request: CodexTaskRequest) -> CodexTaskTurn:
        called["canary"] += 1
        return CodexTaskTurn(
            status="completed",
            error_message=None,
            final_response=FAKE_CANARY_SECRET,
            tokens_in=1,
            tokens_out=1,
            item_types=("AgentMessageThreadItem",),
            events=(
                CodexRuntimeEvent(
                    item_type="AgentMessageThreadItem",
                    payload={"text": FAKE_CANARY_SECRET},
                ),
            ),
        )

    with pytest.raises(LiveGateError, match="credential-isolation canary"):
        run_pair(
            upstream=upstream,
            workspace=workspace,
            output=tmp_path / "failed-canary",
            task_dir=task_dir,
            execute=True,
            receipt_path=receipt_path,
            api_origin="http://127.0.0.1:18000",
            account_reader=lambda: "chatgpt",
            stdio_runner=_stdio_mcp_runner,
            live_canary_runner=failing_canary,
            native_turn_runner=native_runner,
            memory_turn_runner=memory_runner,
            task_contract=local_task_contract(),
        )
    assert called["native"] == 0
    assert called["memory"] == 0
    assert called["canary"] == 1


def test_disallowed_runtime_action_is_a_protocol_violation(tmp_path: Path) -> None:
    workspace = _workspace_with_metadata(tmp_path)

    def runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        return CodexTaskTurn(
            status="completed",
            error_message=None,
            final_response="nope",
            tokens_in=3,
            tokens_out=1,
            item_types=("WebSearchThreadItem",),
            events=(
                CodexRuntimeEvent(
                    item_type="WebSearchThreadItem", payload={"query": "gold answer"}
                ),
            ),
        )

    result = run_agent(
        workspace=workspace,
        output=tmp_path / "out",
        arm=native_arm_configuration(),
        task_id="300",
        task_prompt="Do the task.",
        execute=True,
        turn_runner=runner,
        task_metadata={"output_files": ["report.md", "table.csv", "result.json"]},
        contract=local_task_contract(),
    )
    assert result.failure_class == "protocol_violation"
    assert result.failure_class != "none"


def test_timeout_kills_process_group_and_classifies_receipt(tmp_path: Path) -> None:
    script = tmp_path / "hang.py"
    script.write_text(
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pid = os.fork()\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    supervised = run_process_group(
        argv=(sys.executable, str(script)),
        cwd=tmp_path,
        timeout_seconds=0.2,
        grace_seconds=0.1,
    )
    elapsed = time.monotonic() - started
    assert supervised.timed_out is True
    assert elapsed < 2

    workspace = _workspace_with_metadata(tmp_path)

    def timeout_runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        del request
        return CodexTaskTurn(
            status="timeout",
            error_message="deadline",
            final_response=None,
            tokens_in=2,
            tokens_out=1,
            item_types=(),
            events=(),
            timed_out=True,
        )

    result = run_agent(
        workspace=workspace,
        output=tmp_path / "timeout-out",
        arm=native_arm_configuration(),
        task_id="300",
        task_prompt="Do the task.",
        execute=True,
        turn_runner=timeout_runner,
        contract=local_task_contract(),
        task_metadata={"output_files": ["report.md"]},
    )
    assert result.failure_class == "timeout"
    assert result.timing.timed_out is True
    assert (tmp_path / "timeout-out" / "result.json").is_file()


def test_partial_outputs_are_explicit_failures(tmp_path: Path) -> None:
    workspace = _workspace_with_metadata(tmp_path)

    def runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        out = request.workspace / TARGET_OUTPUT_DIR
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text("# only", encoding="utf-8")
        return CodexTaskTurn(
            status="completed",
            error_message=None,
            final_response=f"['{TARGET_OUTPUT_DIR}/report.md']",
            tokens_in=1,
            tokens_out=1,
            item_types=(),
            events=(),
        )

    result = run_agent(
        workspace=workspace,
        output=tmp_path / "partial",
        arm=native_arm_configuration(),
        task_id="300",
        task_prompt="Do the task.",
        execute=True,
        turn_runner=runner,
        contract=local_task_contract(),
        task_metadata={"output_files": ["report.md", "table.csv", "result.json"]},
    )
    assert result.failure_class == "missing_output"
    assert "table.csv" in result.outputs.missing
    assert (tmp_path / "partial" / "output" / "report.md").is_file()


def test_classification_uses_unredacted_mcp_result_then_redacts_traces(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_metadata(tmp_path)

    def runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        return _successful_turn(
            workspace=request.workspace,
            extra_events=(
                CodexRuntimeEvent(
                    item_type="McpToolCallThreadItem",
                    payload={
                        "server": "remember",
                        "tool": "combined_context",
                        "arguments": {"query": "notes"},
                        "duration_ms": 9,
                        "result": {
                            "items": [{"path": "inputs/notes.txt", "body": "abc"}]
                        },
                    },
                ),
            ),
        )

    from benchmarks.workspacebench.codex import memory_arm_configuration

    result = run_agent(
        workspace=workspace,
        output=tmp_path / "mcp-out",
        arm=memory_arm_configuration(
            remember_bin=remember_launcher(),
            api_origin="http://127.0.0.1:18000",
            enabled_tools=("combined_context",),
        ),
        task_id="300",
        task_prompt="Do the task.",
        execute=True,
        turn_runner=runner,
        contract=local_task_contract(),
        task_metadata={"output_files": ["report.md", "table.csv", "result.json"]},
    )
    assert result.memory is not None
    assert result.memory.returned_context_bytes > 0
    assert result.memory.zero_result_count == 0
    sanitized = (tmp_path / "mcp-out" / "sanitized-codex-trace.jsonl").read_text()
    assert "abc" not in sanitized
    raw = (tmp_path / "mcp-out" / "raw-codex-trace.jsonl").read_text()
    assert "access_token" not in raw


def test_cli_requires_absolute_paths_and_does_not_execute_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _workspace_with_metadata(tmp_path)
    code = workspacebench_main(
        [
            "run-agent",
            "--workspace",
            str(workspace),
            "--output",
            str(tmp_path / "cli-out"),
            "--arm",
            "native",
            "--task-id",
            "300",
            "--prompt",
            "Do the task.",
        ]
    )
    assert code == 1
    result = json.loads((tmp_path / "cli-out" / "result.json").read_text())
    assert result["failure_class"] == "not_executed"
    with pytest.raises(SystemExit) as caught:
        workspacebench_main(
            [
                "preflight",
                "--upstream",
                "relative",
                "--workspace",
                "also",
                "--output",
                "out",
            ]
        )
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "absolute" in err


def test_stdio_rejects_token_in_argv() -> None:
    from benchmarks.workspacebench.mcp import list_tools_over_stdio

    with pytest.raises(Exception, match="token"):
        list_tools_over_stdio(
            argv=("remember", "mcp", "--read-only", "--token", "secret"), cwd=Path("/")
        )


def test_canary_evaluate_helper_exported() -> None:
    assert callable(evaluate_canary)
    assert sha256_bytes(b"x")


def test_live_run_agent_cli_execute_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _workspace_with_metadata(tmp_path)
    code = workspacebench_main(
        [
            "run-agent",
            "--workspace",
            str(workspace),
            "--output",
            str(tmp_path / "cli-exec"),
            "--arm",
            "native",
            "--prompt",
            "Do the task.",
            "--execute",
        ]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "run-pair" in err
    assert not (tmp_path / "cli-exec" / "result.json").exists()


def test_sanitized_env_drops_secret_shaped_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmarks.workspacebench.env import sanitized_subprocess_env

    monkeypatch.setenv("REMEMBER_TOKEN", "secret-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTHORIZATION", "Bearer abc")
    monkeypatch.setenv("MY_PASSWORD", "pw")
    monkeypatch.setenv("APP_SECRET", "shh")
    monkeypatch.setenv("HOME", "/tmp/wb-home")
    monkeypatch.setenv("CODEX_HOME", "/tmp/wb-codex")
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", "/tmp/wb-remember")
    env = sanitized_subprocess_env()
    assert "REMEMBER_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env
    assert "AUTHORIZATION" not in env
    assert "MY_PASSWORD" not in env
    assert "APP_SECRET" not in env
    assert env["HOME"] == "/tmp/wb-home"
    assert "CODEX_HOME" not in env
    assert env["REMEMBER_CONFIG_DIR"] == "/tmp/wb-remember"


def test_mcp_stdio_env_has_no_ambient_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEMBER_TOKEN", "secret-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen: dict[str, object] = {}

    def runner(
        *, argv: Sequence[str], cwd: Path, stdin: str, env=None
    ) -> SupervisedProcessResult:
        seen["env"] = dict(env or {})
        return _stdio_mcp_runner(argv=argv, cwd=cwd, stdin=stdin, env=env)

    discover_mcp(
        api_origin="http://127.0.0.1:18000",
        receipt_origin="http://127.0.0.1:18000",
        stdio_runner=runner,
        cwd=tmp_path,
    )
    env = seen["env"]
    assert isinstance(env, dict)
    assert "REMEMBER_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env


def test_overlapping_roots_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del monkeypatch
    workspace = _role_workspace(tmp_path)
    nested = workspace / "out"
    with pytest.raises(WorkspacePathError, match="overlap"):
        run_preflight(
            upstream=tmp_path / "upstream",
            workspace=workspace,
            output=nested,
            git=lambda args, cwd: UPSTREAM_COMMIT if args[0] == "rev-parse" else "",
            skip_account=True,
            skip_mcp=True,
            skip_office=True,
            arm="native",
        )
    assert not nested.exists()


def test_preflight_creates_output_dir_before_mcp_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    output = tmp_path / "preflight-out"
    seen: dict[str, object] = {}

    def runner(
        *, argv: Sequence[str], cwd: Path, stdin: str, env=None
    ) -> SupervisedProcessResult:
        seen["cwd_exists"] = cwd.is_dir()
        return _stdio_mcp_runner(argv=argv, cwd=cwd, stdin=stdin, env=env)

    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )
    receipt_path = tmp_path / "receipt.json"
    atomic_write_json(path=receipt_path, value=_receipt(digest=tree_digest(workspace)))
    report = run_preflight(
        upstream=upstream,
        workspace=workspace,
        output=output,
        receipt_path=receipt_path,
        api_origin="http://127.0.0.1:18000",
        account_reader=lambda: "chatgpt",
        stdio_runner=runner,
        skip_office=True,
    )
    assert seen.get("cwd_exists") is True
    assert report.mcp is not None
    assert report.mcp.listed_tools[0].input_schema.get("type") == "object"


def test_preflight_requires_exact_task_id_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )
    task = tmp_path / "task"
    task.mkdir()
    (task / "metadata.json").write_text(json.dumps({"task": "x"}), encoding="utf-8")
    report = run_preflight(
        upstream=upstream,
        workspace=workspace,
        output=tmp_path / "out-id",
        task_dir=task,
        skip_account=True,
        skip_mcp=True,
        skip_office=True,
        arm="native",
    )
    assert report.ok is False
    assert any("exact id" in item for item in report.failures)


def test_output_symlink_escape_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace_with_metadata(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("leaked", encoding="utf-8")

    def runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        out = request.workspace / TARGET_OUTPUT_DIR
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").symlink_to(outside)
        return CodexTaskTurn(
            status="completed",
            error_message=None,
            final_response=f"['{TARGET_OUTPUT_DIR}/report.md']",
            tokens_in=1,
            tokens_out=1,
            item_types=(),
            events=(),
        )

    result = run_agent(
        workspace=workspace,
        output=tmp_path / "symlink-out",
        arm=native_arm_configuration(),
        task_id="300",
        task_prompt="Do the task.",
        execute=True,
        turn_runner=runner,
        contract=local_task_contract(),
        task_metadata={"output_files": ["report.md"]},
    )
    assert result.outputs.rejected
    copied = tmp_path / "symlink-out" / "output" / "report.md"
    if copied.exists():
        assert copied.read_text(encoding="utf-8") != "leaked"


def test_unknown_runtime_item_type_is_protocol_violation(tmp_path: Path) -> None:
    workspace = _workspace_with_metadata(tmp_path)

    def runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        turn = _successful_turn(workspace=request.workspace)
        extra = CodexRuntimeEvent(item_type="MadeUpThreadItem", payload={"x": 1})
        return CodexTaskTurn(
            status=turn.status,
            error_message=None,
            final_response=turn.final_response,
            tokens_in=1,
            tokens_out=1,
            item_types=(*turn.item_types, extra.item_type),
            events=(*turn.events, extra),
        )

    result = run_agent(
        workspace=workspace,
        output=tmp_path / "unknown-item",
        arm=native_arm_configuration(),
        task_id="300",
        task_prompt="Do the task.",
        execute=True,
        turn_runner=runner,
        contract=local_task_contract(),
        task_metadata={"output_files": ["report.md", "table.csv", "result.json"]},
    )
    assert result.failure_class == "protocol_violation"
    assert any(
        "unknown runtime item type" in item for item in result.protocol_violations
    )


def test_raw_trace_is_owner_only(tmp_path: Path) -> None:
    workspace = _workspace_with_metadata(tmp_path)

    def runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        return _successful_turn(workspace=request.workspace)

    result = run_agent(
        workspace=workspace,
        output=tmp_path / "perms",
        arm=native_arm_configuration(),
        task_id="300",
        task_prompt="Do the task.",
        execute=True,
        turn_runner=runner,
        contract=local_task_contract(),
        task_metadata={"output_files": ["report.md", "table.csv", "result.json"]},
    )
    raw = tmp_path / "perms" / result.traces.raw_relpath
    mode = raw.stat().st_mode & 0o777
    assert mode == 0o600


def test_pinned_helper_loads_when_yaml_is_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workspacebench.task_contract import AGENT_RUNNER_RELPATH
    from benchmarks.workspacebench.task_contract import load_upstream_task_contract

    source = """
import yaml
from tqdm import tqdm
from filesys_utils import filesys_rollback
import agent_as_a_judge

def _wrap_prompt(*, prompt, work_dir, prompt_head, prompt_tail, task_target_output_dir, language):
    return "wrapped:" + prompt

def _expected_output_files(meta):
    return ["report.md"]

def _copy_from_manifest(meta, *, work_dir):
    return []

def _collect_output_paths(*, task_target_output_dir, work_dir, expected_files, returned_paths, last_text, min_mtime):
    return [], []

def _copy_outputs(*, output_paths, out_dir, preserve_root=None):
    return []
"""
    upstream = tmp_path / "upstream"
    path = upstream / AGENT_RUNNER_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    digest = sha256_file(path)
    from benchmarks.workspacebench.task_contract import EXPECTED_UPSTREAM_FILE_SHA256

    monkeypatch.setitem(EXPECTED_UPSTREAM_FILE_SHA256, AGENT_RUNNER_RELPATH, digest)
    import sys

    before = dict(sys.modules)
    contract = load_upstream_task_contract(upstream=upstream)
    assert "wrapped:" in contract.wrap_prompt(
        task_prompt="hi", work_dir=tmp_path, target_output_dir="model_output"
    )
    assert sys.modules.get("filesys_utils") == before.get("filesys_utils")
    assert sys.modules.get("agent_as_a_judge") == before.get("agent_as_a_judge")


def test_duplicate_mcp_tool_names_are_rejected() -> None:
    from benchmarks.workspacebench.mcp import bind_listed_tools
    from benchmarks.workspacebench.mcp import McpDiscoveryError
    from benchmarks.workspacebench.models import ToolDescriptorRecord

    with pytest.raises(McpDiscoveryError, match="duplicate"):
        bind_listed_tools(
            records=(
                ToolDescriptorRecord(
                    name="resolve_entity",
                    description="a",
                    input_schema={"type": "object"},
                ),
                ToolDescriptorRecord(
                    name="resolve_entity",
                    description="b",
                    input_schema={"type": "object"},
                ),
                ToolDescriptorRecord(name="claims_and_sources_context"),
                ToolDescriptorRecord(name="facts_context"),
                ToolDescriptorRecord(name="combined_context"),
            ),
            api_origin="http://127.0.0.1:18000",
            discovered_over_stdio=True,
        )


def test_required_assured_tools_match_canonical_registry_and_descriptors() -> None:
    """Workspace-Bench preflight pins the shipping registry, not a local copy."""
    from benchmarks.workspacebench.consumption import MEMORY_CONSUMPTION_INSTRUCTION
    from benchmarks.workspacebench.mcp import bind_listed_tools
    from benchmarks.workspacebench.mcp import canonical_assured_operation_names
    from benchmarks.workspacebench.models import ToolDescriptorRecord

    from rememberstack.model import AssuredOperationName
    from rememberstack.spine.assured_operations import CANONICAL_OPERATIONS
    from rememberstack.surfaces.operation_surface import operation_descriptors

    registry_names = canonical_assured_operation_names()
    enum_names = tuple(name.value for name in AssuredOperationName)
    descriptors = operation_descriptors(operations=CANONICAL_OPERATIONS)
    descriptor_names = tuple(descriptor.name for descriptor in descriptors)
    assert REQUIRED_ASSURED_TOOLS == registry_names
    assert REQUIRED_ASSURED_TOOLS == (
        "resolve_entity",
        "claims_and_sources_context",
        "facts_context",
        "combined_context",
    )
    assert set(REQUIRED_ASSURED_TOOLS) == set(enum_names)
    assert descriptor_names == REQUIRED_ASSURED_TOOLS
    combined = next(
        operation
        for operation in CANONICAL_OPERATIONS
        if operation.name is AssuredOperationName.COMBINED_CONTEXT
    )
    assert combined.result_contract == "context_bundle_v2"
    properties = combined.result_schema.get("properties")
    assert isinstance(properties, dict)
    assert "claims_and_sources" in properties
    assert "facts" in properties
    for name in REQUIRED_ASSURED_TOOLS:
        assert f"`{name}`" in MEMORY_CONSUMPTION_INSTRUCTION
    assert "`ContextBundle/v2`" in MEMORY_CONSUMPTION_INSTRUCTION
    assert "`claims_and_sources` and `facts` child envelopes" in (
        MEMORY_CONSUMPTION_INSTRUCTION
    )
    discovery = bind_listed_tools(
        records=tuple(
            ToolDescriptorRecord(
                name=descriptor.name,
                description=descriptor.description,
                input_schema=descriptor.input_schema,
            )
            for descriptor in descriptors
        ),
        api_origin="http://127.0.0.1:18000",
        discovered_over_stdio=True,
    )
    assert discovery.assured_operations == REQUIRED_ASSURED_TOOLS
    assert discovery.enabled_tools == REQUIRED_ASSURED_TOOLS


def test_stale_assured_catalog_is_rejected() -> None:
    """A catalog that still advertises renamed operations fails preflight."""
    from benchmarks.workspacebench.mcp import bind_listed_tools
    from benchmarks.workspacebench.mcp import McpDiscoveryError
    from benchmarks.workspacebench.models import ToolDescriptorRecord

    with pytest.raises(
        McpDiscoveryError, match="required read tools missing from MCP catalog"
    ):
        bind_listed_tools(
            records=(
                ToolDescriptorRecord(name="resolve_entity"),
                ToolDescriptorRecord(name="testimony_context"),
                ToolDescriptorRecord(name="fact_context"),
                ToolDescriptorRecord(name="answer_context"),
            ),
            api_origin="http://127.0.0.1:18000",
            discovered_over_stdio=True,
        )


def test_registry_name_drift_fails_catalog_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later registry rename must fail Workspace-Bench catalog binding."""
    from benchmarks.workspacebench import mcp as mcp_mod
    from benchmarks.workspacebench.mcp import bind_listed_tools
    from benchmarks.workspacebench.mcp import McpDiscoveryError
    from benchmarks.workspacebench.models import ToolDescriptorRecord

    monkeypatch.setattr(
        mcp_mod,
        "canonical_assured_operation_names",
        lambda: ("resolve_entity", "renamed_context"),
    )
    with pytest.raises(McpDiscoveryError, match="drifted from the canonical registry"):
        bind_listed_tools(
            records=tuple(
                ToolDescriptorRecord(name=name) for name in REQUIRED_ASSURED_TOOLS
            ),
            api_origin="http://127.0.0.1:18000",
            discovered_over_stdio=True,
        )


def test_protocol_fingerprint_includes_catalog_and_receipt(tmp_path: Path) -> None:
    from benchmarks.workspacebench.fingerprint import protocol_fingerprint
    from benchmarks.workspacebench.models import McpDiscovery
    from benchmarks.workspacebench.models import ToolDescriptorRecord

    receipt = _receipt(digest="a" * 64)
    mcp = McpDiscovery(
        api_origin="http://127.0.0.1:18000",
        assured_operations=("resolve_entity",),
        open_query_tools=(),
        listed_tools=(
            ToolDescriptorRecord(
                name="resolve_entity",
                description="d",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            ),
        ),
        enabled_tools=("resolve_entity",),
        catalog_sha256="c" * 64,
    )
    first = protocol_fingerprint(
        workspace_digest="a" * 64,
        task_id="300",
        receipt=receipt,
        mcp=mcp,
        mcp_catalog_sha256="c" * 64,
        codex_cli_sha256="a" * 64,
    )
    second = protocol_fingerprint(
        workspace_digest="a" * 64,
        task_id="300",
        receipt=receipt,
        mcp_catalog_sha256="d" * 64,
        codex_cli_sha256="a" * 64,
    )
    without_receipt = protocol_fingerprint(
        workspace_digest="a" * 64,
        task_id="300",
        mcp_catalog_sha256="c" * 64,
        codex_cli_sha256="a" * 64,
    )
    other_binary = protocol_fingerprint(
        workspace_digest="a" * 64,
        task_id="300",
        receipt=receipt,
        mcp=mcp,
        mcp_catalog_sha256="c" * 64,
        codex_cli_sha256="b" * 64,
    )
    assert first != second
    assert first != without_receipt
    assert first != other_binary


def test_pinned_judge_view_sees_source_output_and_trace(tmp_path: Path) -> None:
    from benchmarks.workspacebench.judge import pinned_build_trace_snapshot
    from benchmarks.workspacebench.judge import pinned_resolve_original_task_source

    workspace = _workspace_with_metadata(tmp_path)
    task_dir = _task_dir(tmp_path)

    def runner(*, request: CodexTaskRequest) -> CodexTaskTurn:
        return _successful_turn(workspace=request.workspace)

    result = run_agent(
        workspace=workspace,
        output=tmp_path / "judge-case",
        arm=native_arm_configuration(),
        task_id="300",
        task_prompt="Do the task.",
        execute=True,
        turn_runner=runner,
        contract=local_task_contract(),
        task_metadata=json.loads((task_dir / "metadata.json").read_text()),
        source_task_dir=task_dir,
        case_dir=tmp_path / "judge-case",
    )
    assert result.failure_class == "none"
    meta = json.loads((tmp_path / "judge-case" / "metadata.json").read_text())
    source = pinned_resolve_original_task_source(meta)
    assert source is not None
    assert source == task_dir.resolve()
    assert (source / "data" / "notes.txt").is_file()
    snapshot = pinned_build_trace_snapshot(task_dir=tmp_path / "judge-case")
    assert snapshot["events"]
    assert any(item.get("type") == "tool" for item in snapshot["events"])  # type: ignore[union-attr]
    assert (tmp_path / "judge-case" / "output" / "report.md").is_file()


def test_live_execute_cannot_skip_office(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    task_dir = _task_dir(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    receipt_path = tmp_path / "receipt.json"
    atomic_write_json(path=receipt_path, value=_receipt(digest=tree_digest(workspace)))
    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )
    with pytest.raises(LiveGateError, match="cannot skip office"):
        run_pair(
            upstream=upstream,
            workspace=workspace,
            output=tmp_path / "skip-office",
            task_dir=task_dir,
            execute=True,
            receipt_path=receipt_path,
            api_origin="http://127.0.0.1:18000",
            skip_office=True,
            account_reader=lambda: "chatgpt",
            stdio_runner=_stdio_mcp_runner,
            live_canary_runner=_passing_canary,
            native_turn_runner=lambda **_: _successful_turn(workspace=workspace),
            memory_turn_runner=lambda **_: _successful_turn(workspace=workspace),
            task_contract=local_task_contract(),
        )


def test_live_execute_requires_task_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _plant_office(upstream, monkeypatch)
    receipt_path = tmp_path / "receipt.json"
    atomic_write_json(path=receipt_path, value=_receipt(digest=tree_digest(workspace)))
    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )
    with pytest.raises(LiveGateError, match="task-dir"):
        run_pair(
            upstream=upstream,
            workspace=workspace,
            output=tmp_path / "no-task-dir",
            execute=True,
            receipt_path=receipt_path,
            api_origin="http://127.0.0.1:18000",
            account_reader=lambda: "chatgpt",
            stdio_runner=_stdio_mcp_runner,
            live_canary_runner=_passing_canary,
            native_turn_runner=lambda **_: _successful_turn(workspace=workspace),
            memory_turn_runner=lambda **_: _successful_turn(workspace=workspace),
            task_contract=local_task_contract(),
        )


def test_task_dir_nested_under_upstream_passes_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    upstream = _pin_upstream(tmp_path, monkeypatch)
    task_dir = upstream / "evaluation" / "tasks_lite" / "300"
    source_task = _task_dir(tmp_path)
    task_dir.mkdir(parents=True)
    (task_dir / "metadata.json").write_bytes(
        (source_task / "metadata.json").read_bytes()
    )
    (task_dir / "data").mkdir()
    (task_dir / "data" / "notes.txt").write_text("staged-notes", encoding="utf-8")

    def git(args: Sequence[str], cwd: Path) -> str:
        del cwd
        if args[0] == "rev-parse":
            return UPSTREAM_COMMIT + "\n"
        return ""

    report = run_preflight(
        upstream=upstream,
        workspace=workspace,
        output=tmp_path / "nested-task-out",
        task_dir=task_dir,
        git=git,
        skip_account=True,
        skip_mcp=True,
        skip_office=True,
        arm="native",
    )
    assert report.ok is True
    assert (tmp_path / "nested-task-out").is_dir()
    assert report.codex_runtime is not None
    assert report.codex_runtime.cli_version == "0.147.0"
    assert report.codex_runtime.isolation == "disposable_codex_home_keyring"
    assert report.protocol.codex_cli_sha256 == report.codex_runtime.cli_sha256


def test_output_nested_in_workspace_is_rejected_without_creating_output(
    tmp_path: Path,
) -> None:
    workspace = _role_workspace(tmp_path)
    nested = workspace / "pair-out"
    assert not nested.exists()
    with pytest.raises(WorkspacePathError, match="overlap"):
        run_pair(
            upstream=tmp_path / "upstream",
            workspace=workspace,
            output=nested,
            task_dir=_task_dir(tmp_path),
            execute=False,
            skip_account=True,
            skip_mcp=True,
            skip_office=True,
        )
    assert not nested.exists()
    assert not any(path.name == "pair-out" for path in workspace.iterdir())


def test_isolated_exec_prefix_drops_ambient_secrets_from_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workspacebench.codex import isolated_codex_env
    from benchmarks.workspacebench.codex import native_arm_configuration
    from benchmarks.workspacebench.isolation import isolated_command_argv
    from benchmarks.workspacebench.isolation import prepare_disposable_codex_home
    from benchmarks.workspacebench.isolation import TRUSTED_ENV_BIN

    secret = "sk-ambient-secret"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("USER_SECRET_TOKEN", "tok-ambient")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "user-codex"))
    isolation = prepare_disposable_codex_home(tmp_path / "wb-codex-home")
    intended = isolated_codex_env(isolation=isolation, arm=native_arm_configuration())
    assert "OPENAI_API_KEY" not in intended
    assert "USER_SECRET_TOKEN" not in intended
    assert intended["CODEX_HOME"] == str(isolation.home)
    argv = isolated_command_argv(
        env=intended,
        binary=Path(sys.executable),
        args=("-c", "import json, os; print(json.dumps(dict(os.environ)))"),
    )
    assert argv[0] == str(TRUSTED_ENV_BIN)
    assert argv[1] == "-i"
    assert secret not in " ".join(argv)
    assert "OPENAI_API_KEY" not in argv
    assert "USER_SECRET_TOKEN" not in argv
    assert "auth.json" not in " ".join(argv)
    completed = subprocess.run(  # noqa: S603 -- isolation prefix, harmless python -c
        list(argv), check=False, capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 0, completed.stderr
    child_env = json.loads(completed.stdout)
    assert "OPENAI_API_KEY" not in child_env
    assert "USER_SECRET_TOKEN" not in child_env
    assert secret not in completed.stdout
    assert child_env["CODEX_HOME"] == str(isolation.home)


def test_user_mcp_and_config_cannot_enter_either_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workspacebench.codex import app_server_launch_argv
    from benchmarks.workspacebench.codex import isolated_codex_env
    from benchmarks.workspacebench.codex import memory_arm_configuration
    from benchmarks.workspacebench.codex import native_arm_configuration
    from benchmarks.workspacebench.isolation import inspect_bundled_codex_cli
    from benchmarks.workspacebench.isolation import prepare_disposable_codex_home
    from benchmarks.workspacebench.isolation import resolve_bundled_codex_binary
    from benchmarks.workspacebench.isolation import TRUSTED_ENV_BIN

    user_home = tmp_path / "user-codex"
    user_home.mkdir()
    (user_home / "config.toml").write_text(
        '[mcp_servers.evil]\ncommand = "npx"\nargs = ["-y", "evil-mcp"]\n'
        "enabled = true\n",
        encoding="utf-8",
    )
    (user_home / "AGENTS.md").write_text(
        "operator global instructions\n", encoding="utf-8"
    )
    (user_home / "hooks").mkdir()
    (user_home / "plugins").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(user_home))
    isolation = prepare_disposable_codex_home(tmp_path / "wb-codex-home")
    inspect_bundled_codex_cli()
    binary = resolve_bundled_codex_binary()
    native = native_arm_configuration()
    memory = memory_arm_configuration(
        remember_bin=("/usr/bin/python", "-m", "remember"),
        api_origin="http://127.0.0.1:18000",
        enabled_tools=("resolve_entity", "facts_context"),
    )
    native_env = isolated_codex_env(isolation=isolation, arm=native)
    memory_env = isolated_codex_env(isolation=isolation, arm=memory)
    native_argv = app_server_launch_argv(binary=binary, arm=native, env=native_env)
    memory_argv = app_server_launch_argv(binary=binary, arm=memory, env=memory_env)
    native_blob = " ".join(native_argv)
    memory_blob = " ".join(memory_argv)
    assert native_argv[0] == str(TRUSTED_ENV_BIN)
    assert native_argv[1] == "-i"
    assert memory_argv[0] == str(TRUSTED_ENV_BIN)
    assert str(binary) in native_argv
    assert str(binary) in memory_argv
    assert "evil" not in native_blob
    assert "evil" not in memory_blob
    assert "mcp_servers.remember" not in native_blob
    assert "mcp_servers.remember" in memory_blob
    assert "--ignore-user-config" not in native_argv
    assert "--ignore-user-config" not in memory_argv
    assert "auth.json" not in native_blob
    assert "auth.json" not in memory_blob
    assert "evil" not in isolation.config_toml
    assert "[mcp_servers" not in isolation.config_toml
    assert not (isolation.home / "AGENTS.md").exists()
    assert not (isolation.home / "hooks").exists()
    assert native_env["CODEX_HOME"] == str(isolation.home)
    assert memory_env["CODEX_HOME"] == str(isolation.home)
    assert native_env["CODEX_HOME"] != str(user_home)
    assert "REMEMBER_CONFIG_DIR" not in native_env
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "remember-config"))
    memory_env = isolated_codex_env(isolation=isolation, arm=memory)
    native_env = isolated_codex_env(isolation=isolation, arm=native)
    assert memory_env.get("REMEMBER_CONFIG_DIR") == str(tmp_path / "remember-config")
    assert "REMEMBER_CONFIG_DIR" not in native_env


def test_bundled_codex_cli_accepts_generated_app_server_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workspacebench.codex import app_server_help_argv
    from benchmarks.workspacebench.codex import isolated_codex_env
    from benchmarks.workspacebench.codex import memory_arm_configuration
    from benchmarks.workspacebench.codex import native_arm_configuration
    from benchmarks.workspacebench.isolation import inspect_bundled_codex_cli
    from benchmarks.workspacebench.isolation import prepare_disposable_codex_home
    from benchmarks.workspacebench.isolation import resolve_bundled_codex_binary
    from benchmarks.workspacebench.isolation import TRUSTED_ENV_BIN
    from benchmarks.workspacebench.protocol import CODEX_CLI_VERSION

    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-secret")
    pin = inspect_bundled_codex_cli()
    assert pin.cli_version == CODEX_CLI_VERSION
    assert pin.sdk_version == CODEX_CLI_VERSION
    assert pin.cli_sha256
    binary = resolve_bundled_codex_binary()
    isolation = prepare_disposable_codex_home(tmp_path / "wb-codex-home")
    native = native_arm_configuration()
    memory = memory_arm_configuration(
        remember_bin=("/usr/bin/python", "-m", "remember"),
        api_origin="http://127.0.0.1:18000",
        enabled_tools=("resolve_entity", "facts_context"),
    )
    for arm in (native, memory):
        env = isolated_codex_env(isolation=isolation, arm=arm)
        argv = app_server_help_argv(binary=binary, arm=arm, env=env)
        assert "--ignore-user-config" not in argv
        assert argv[0] == str(TRUSTED_ENV_BIN)
        assert argv[1] == "-i"
        assert str(binary) in argv
        assert argv[-2:] == ("app-server", "--help")
        assert "OPENAI_API_KEY" not in argv
        completed = subprocess.run(  # noqa: S603 -- isolation prefix, generated argv
            list(argv), check=False, capture_output=True, text=True, timeout=30
        )
        assert completed.returncode == 0, completed.stderr + completed.stdout
    rejected = subprocess.run(  # noqa: S603 -- proves unsupported flag still exits 2
        [str(binary), "--ignore-user-config", "app-server", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=isolated_codex_env(isolation=isolation, arm=native),
    )
    assert rejected.returncode == 2
    assert "unexpected argument '--ignore-user-config'" in rejected.stderr


def test_live_canary_uses_memory_arm_launch_boundary(tmp_path: Path) -> None:
    from benchmarks.workspacebench.canary import require_protocol_canary_request
    from benchmarks.workspacebench.codex import app_server_launch_argv
    from benchmarks.workspacebench.codex import CodexTaskRequest
    from benchmarks.workspacebench.codex import isolated_codex_env
    from benchmarks.workspacebench.codex import memory_arm_configuration
    from benchmarks.workspacebench.codex import native_arm_configuration
    from benchmarks.workspacebench.isolation import inspect_bundled_codex_cli
    from benchmarks.workspacebench.isolation import prepare_disposable_codex_home
    from benchmarks.workspacebench.isolation import resolve_bundled_codex_binary
    from benchmarks.workspacebench.protocol import CODEX_MODEL
    from benchmarks.workspacebench.protocol import CODEX_REASONING_EFFORT

    inspect_bundled_codex_cli()
    binary = resolve_bundled_codex_binary()
    isolation = prepare_disposable_codex_home(tmp_path / "wb-codex-home")
    memory = memory_arm_configuration(
        remember_bin=("/usr/bin/python", "-m", "remember"),
        api_origin="http://127.0.0.1:18000",
        enabled_tools=("resolve_entity",),
    )
    native = native_arm_configuration()
    memory_env = isolated_codex_env(isolation=isolation, arm=memory)
    native_env = isolated_codex_env(isolation=isolation, arm=native)
    request = CodexTaskRequest(
        prompt="canary",
        workspace=Path("/tmp/wb-canary-ws"),
        model=CODEX_MODEL,
        reasoning_effort=CODEX_REASONING_EFFORT,
        arm=memory,
        timeout_seconds=60,
        grace_seconds=5,
    )
    require_protocol_canary_request(request)
    memory_argv = app_server_launch_argv(binary=binary, arm=memory, env=memory_env)
    native_argv = app_server_launch_argv(binary=binary, arm=native, env=native_env)
    assert memory_argv == app_server_launch_argv(
        binary=binary, arm=request.arm, env=memory_env
    )
    assert "mcp_servers.remember" in " ".join(memory_argv)
    assert "mcp_servers.remember" not in " ".join(native_argv)
    assert memory_argv[0] == native_argv[0] == "/usr/bin/env"


def test_codex_isolation_does_not_mutate_parent_os_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import benchmarks.workspacebench.codex as codex_mod
    from benchmarks.workspacebench.codex import isolated_codex_env
    from benchmarks.workspacebench.codex import native_arm_configuration
    import benchmarks.workspacebench.isolation as isolation_mod
    from benchmarks.workspacebench.isolation import isolated_command_argv
    from benchmarks.workspacebench.isolation import prepare_disposable_codex_home

    assert not hasattr(isolation_mod, "replace_os_environ")
    assert not hasattr(isolation_mod, "sdk_merged_child_env")
    assert "replace_os_environ" not in inspect.getsource(codex_mod)
    assert "os.environ.clear" not in inspect.getsource(isolation_mod)
    assert "os.environ.clear" not in inspect.getsource(codex_mod)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-secret")
    monkeypatch.setenv("WB_PARENT_MARKER", "keep-me")
    parent_before = dict(os.environ)  # noqa: TID251 -- prove parent env is unchanged
    isolation = prepare_disposable_codex_home(tmp_path / "wb-codex-home")
    intended = isolated_codex_env(isolation=isolation, arm=native_arm_configuration())
    argv = isolated_command_argv(
        env=intended,
        binary=Path(sys.executable),
        args=("-c", "import time; time.sleep(0.05)"),
    )
    observed: list[dict[str, str]] = []
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            current = dict(os.environ)  # noqa: TID251 -- concurrent parent snapshot
            if current != parent_before:
                observed.append(current)
            time.sleep(0.001)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        completed = subprocess.run(  # noqa: S603 -- isolation prefix, harmless sleep
            list(argv), check=False, capture_output=True, text=True, timeout=30
        )
    finally:
        stop.set()
        watcher.join(timeout=2)
    assert completed.returncode == 0, completed.stderr
    assert observed == []
    assert dict(os.environ) == parent_before  # noqa: TID251 -- parent env unchanged
    assert parent_before["OPENAI_API_KEY"] == "sk-parent-secret"
    assert parent_before["WB_PARENT_MARKER"] == "keep-me"


def test_execute_fails_closed_when_cli_hash_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workspacebench.isolation import CodexRuntimeInspection

    workspace = _role_workspace(tmp_path)
    task_dir = _task_dir(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _plant_office(upstream, monkeypatch)
    receipt_path = tmp_path / "receipt.json"
    atomic_write_json(path=receipt_path, value=_receipt(digest=tree_digest(workspace)))
    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )

    def fake_inspect(*, force: bool = False) -> CodexRuntimeInspection:
        digest = ("b" if force else "a") * 64
        return CodexRuntimeInspection(
            sdk_version="0.147.0",
            cli_version="0.147.0",
            cli_sha256=digest,
            isolation="disposable_codex_home_keyring",
            credentials_store="keyring",
        )

    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_bundled_codex_cli", fake_inspect
    )
    monkeypatch.setattr(
        "benchmarks.workspacebench.pair.inspect_bundled_codex_cli", fake_inspect
    )
    with pytest.raises(LiveGateError, match="SHA-256 changed"):
        run_pair(
            upstream=upstream,
            workspace=workspace,
            output=tmp_path / "hash-drift",
            task_dir=task_dir,
            execute=True,
            receipt_path=receipt_path,
            api_origin="http://127.0.0.1:18000",
            account_reader=lambda: "chatgpt",
            stdio_runner=_stdio_mcp_runner,
            live_canary_runner=_passing_canary,
            native_turn_runner=lambda **_: _successful_turn(workspace=workspace),
            memory_turn_runner=lambda **_: _successful_turn(workspace=workspace),
            task_contract=local_task_contract(),
        )


def test_execute_fails_closed_when_runtime_pin_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workspacebench.errors import WorkspaceBenchError as BenchError
    from benchmarks.workspacebench.isolation import CodexIsolationError

    workspace = _role_workspace(tmp_path)
    task_dir = _task_dir(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _plant_office(upstream, monkeypatch)
    receipt_path = tmp_path / "receipt.json"
    atomic_write_json(path=receipt_path, value=_receipt(digest=tree_digest(workspace)))
    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )

    def missing_pin(*, force: bool = False) -> object:
        del force
        raise CodexIsolationError("bundled Codex CLI is not a file: missing")

    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_bundled_codex_cli", missing_pin
    )
    with pytest.raises(BenchError, match="preflight failed"):
        run_pair(
            upstream=upstream,
            workspace=workspace,
            output=tmp_path / "missing-pin",
            task_dir=task_dir,
            execute=True,
            receipt_path=receipt_path,
            api_origin="http://127.0.0.1:18000",
            account_reader=lambda: "chatgpt",
            stdio_runner=_stdio_mcp_runner,
            live_canary_runner=_passing_canary,
            native_turn_runner=lambda **_: _successful_turn(workspace=workspace),
            memory_turn_runner=lambda **_: _successful_turn(workspace=workspace),
            task_contract=local_task_contract(),
        )


def test_pair_rebuild_keeps_preflight_cli_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _role_workspace(tmp_path)
    task_dir = _task_dir(tmp_path)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _plant_office(upstream, monkeypatch)
    receipt_path = tmp_path / "receipt.json"
    atomic_write_json(path=receipt_path, value=_receipt(digest=tree_digest(workspace)))
    monkeypatch.setattr(
        "benchmarks.workspacebench.preflight.inspect_upstream",
        lambda **_kwargs: UpstreamInspection(
            path=str(upstream), commit=UPSTREAM_COMMIT, dirty=False, file_sha256={}
        ),
    )
    preflight, pair, native, memory = run_pair(
        upstream=upstream,
        workspace=workspace,
        output=tmp_path / "rebuild-pin",
        task_dir=task_dir,
        execute=False,
        receipt_path=receipt_path,
        api_origin="http://127.0.0.1:18000",
        account_reader=lambda: "chatgpt",
        stdio_runner=_stdio_mcp_runner,
        skip_account=True,
        task_contract=local_task_contract(),
    )
    assert preflight.codex_runtime is not None
    pin = preflight.codex_runtime.cli_sha256
    assert preflight.protocol.codex_cli_sha256 == pin
    assert native.protocol.codex_cli_sha256 == pin
    assert memory.protocol.codex_cli_sha256 == pin
    assert native.protocol.protocol_fingerprint == pair.protocol_fingerprint
    from benchmarks.workspacebench.fingerprint import protocol_fingerprint

    drifted = protocol_fingerprint(
        workspace_digest=native.protocol.workspace_digest,
        task_id="300",
        role_workspace_digest=native.protocol.role_workspace_digest,
        staged_workspace_digest=native.protocol.staged_workspace_digest,
        data_manifest_sha256=native.protocol.data_manifest_sha256,
        task_prompt_sha256=native.protocol.task_prompt_sha256,
        task_metadata_sha256=native.protocol.task_metadata_sha256,
        office_skill_digest=native.protocol.office_skill_digest,
        overlay_sha256=native.protocol.overlay_manifest_sha256,
        upstream_file_sha256=preflight.upstream.file_sha256,
        canonical_api_origin=native.protocol.canonical_api_origin,
        local_api_origin=native.protocol.local_api_origin,
        receipt=preflight.receipt,
        access=None if preflight.mcp is None else preflight.mcp.access,
        mcp_catalog_sha256=native.protocol.mcp_catalog_sha256,
        timeout_seconds=native.protocol.timeout_seconds,
        grace_seconds=native.protocol.grace_seconds,
        codex_cli_sha256="0" * 64,
    )
    assert drifted != pair.protocol_fingerprint
